"""ClawMetry Physical AI page (/robotics) integration: a live controller with Stop, plus a robot-trace journal.

What the page reads (from clawmetry_pro 0.7.66 source, see docs/CLAWMETRY-ROBOTICS-INTEGRATION.md):
  * Controllers: a private registry file ~/.clawmetry/robotics/control/<controller_id>.json (dir 0700, file 0600)
    holding {controller_id, port, token, metadata}. ClawMetry POSTs /status, /stop, /reset to
    http://127.0.0.1:<port>/ with "Authorization: Bearer <token>" and a JSON body naming the controller_id.
  * Traces: JSONL journal files ~/.clawmetry/robotics/journal/<run_id>.jsonl, schema clawmetry.robotics.v1,
    ingested once a second by the ClawMetry sync daemon. The page does NOT read OTLP spans.

Stop -> Skills.pause("clawmetry_stop") -> bridge.stop_and_settle(), run on a worker thread so ClawMetry's 0.6 s
request timeout is never the thing that decides whether the robot stopped. Every Stop/Reset is logged (stderr +
journal control.* events). Reset clears the latch and calls Skills.resume(): it re-allows skill calls; it never
moves the robot by itself.

Robot sessions (docs/ROBOT-SESSIONS.md): the controller is also a trace sink of the Skills object. Each robot task
is a session made of turns; each turn (one skill call) is written as turn.observe, turn.decide, then the gate's
predict / breach_predicted / turn.command events, then deviation, turn.feedback and turn.outcome. A session opens on
the first skill call after an idle gap and closes on hold_pose("...handover..."), a successful place, or
SESSION_IDLE_S without a skill call. The monitor's breach events and policy.changed land in the same journal.

Pure stdlib; no dependency on the clawmetry_pro package. Disable with CLAWMETRY_CONTROLLER=0.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import math
import os
import secrets
import stat
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bridge.base import HARDWARE_BODIES

log = logging.getLogger("clawmetry.robotics")
if not log.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("[clawmetry-robotics] %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)

SCHEMA = "clawmetry.robotics.v1"
STATIONARY_MODES = {"paused", "standing", "idle", "damped"}
CONFIRM_FRESH_NS = 1_000_000_000  # ClawMetry treats a stop confirmation older than 1 s as stale
SESSION_IDLE_S = 30.0
THUMB_SIZE = (320, 180)
FRAME_SIZE = (320, 180)  # replay frames: Pro renders camera.frame JPEGs up to 32 KiB / 44000 base64 chars
FRAME_MAX_BYTES = 32768
FRAME_BUDGET_BYTES = int(os.environ.get("CLAWMETRY_FRAMES_BUDGET_MB", "64")) * 1024 * 1024
TASK_TEXT_MAX = 500
TASK_WHO_MAX = 100
# skill-side event kind -> journal kind (everything else is written under its own name)
KIND = {"command": "turn.command"}


def _base() -> Path:
    return Path.home() / ".clawmetry" / "robotics"


def _private_dir(p: Path) -> Path:
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = p.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise PermissionError(f"{p} must be a private (0700) directory owned by this user")
    return p


class Journal:
    """Append-only clawmetry.robotics.v1 event log for one run."""

    def __init__(self, robot_id: str, environment: str, directory: Path | None = None):
        self.run_id = uuid.uuid4().hex
        self.robot_id, self.environment = robot_id[:200], environment
        d = _private_dir(
            Path(os.environ.get("CLAWMETRY_ROBOTICS_DIR") or _base()) / "journal" if directory is None else directory
        )
        self.path = d / f"{self.run_id}.jsonl"
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)
        self._f = os.fdopen(fd, "a", buffering=1)
        self._seq = 0
        self._lock = threading.Lock()

    def emit(self, kind: str, **data):
        with self._lock:
            if self._f.closed:
                return
            self._seq += 1
            ev = {
                "schema": SCHEMA,
                "run_id": self.run_id,
                "sequence": self._seq,
                "wall_time_ns": str(time.time_ns()),
                "monotonic_ns": str(time.monotonic_ns()),
                "environment": self.environment,
                "robot_id": self.robot_id,
                "kind": kind[:80],
                "data": data,
                "dropped_total": 0,
            }
            self._f.write(json.dumps(ev, default=str, allow_nan=False) + "\n")

    def close(self):
        with self._lock:
            self._f.close()


class SkillsController:
    """A ClawMetry live controller for the skills API. capabilities.physical_stop is True: Stop calls
    bridge.stop_and_settle (the vendor's own StopMove / zero-velocity path on the real robots), and confirmation is
    only reported from fresh bridge state showing a stationary, balanced body."""

    def __init__(
        self,
        skills,
        directory: Path | None = None,
        journal_dir: Path | None = None,
        observe_hz: float = 1.0,
        thumbnails: bool | None = None,
        frames_hz: float | None = None,
        agent=None,
    ):
        self.skills = skills
        b = skills.bridge
        self._session: dict | None = None
        self._turn: dict | None = None
        # requests typed into ClawMetry's Physical AI console; one at a time, cancelled by Stop
        self._task: dict | None = None
        self._task_cancel = threading.Event()
        self._task_worker: threading.Thread | None = None
        self._agent = agent
        self._last_activity = 0.0
        self._slock = threading.RLock()
        # demo_cam thumbnails: sim bodies with a camera API (Isaac). Off for fake / hardware bodies.
        self.thumbnails = (
            callable(getattr(b, "screenshot", None)) and b.body not in (*HARDWARE_BODIES, "fake")
            if thumbnails is None
            else thumbnails
        )
        # replay frames from demo_cam into the journal (camera.frame): on wherever thumbnails are, CLAWMETRY_FRAMES_HZ=0 to disable
        hz = float(os.environ.get("CLAWMETRY_FRAMES_HZ", "0.5")) if frames_hz is None else float(frames_hz)
        self._frame_period = 1.0 / hz if (hz > 0 and self.thumbnails) else 0.0
        self._frame_next = 0.0
        self._frame_bytes = 0
        self._frame_count = 0
        self._frames_full = False
        spec = getattr(b, "spec", None) or {}  # vendor bodies (gallery): one robot per vendor on the page
        self.robot_id = f"leukerbad-{b.body}-{spec['name']}" if spec.get("name") else f"leukerbad-{b.body}"
        self.robot_type = (f"{spec.get('vendor', '')} {spec.get('product', '')}".strip() or b.body) if spec else b.body
        self.environment = "hardware" if b.body in HARDWARE_BODIES else "simulation"
        self.controller_id = uuid.uuid4().hex
        self._token = secrets.token_urlsafe(32)
        self._lock = threading.RLock()
        self.generation = 0
        self._worker: threading.Thread | None = None
        self._status = {"state": "ready", "message": "Ready", "physical_stop_confirmed": False}
        self._closed = False
        try:
            self.journal = Journal(self.robot_id, self.environment, journal_dir)
        except Exception as e:  # noqa: BLE001  (traces are optional; Stop must still register)
            log.warning("journal disabled: %s", e)
            self.journal = None
        ctl = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *_):
                pass

            def do_POST(self):
                if self.headers.get("Origin") or not hmac.compare_digest(
                    self.headers.get("Authorization", "").encode(), ("Bearer " + ctl._token).encode()
                ):
                    self.send_error(403)
                    return
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= n <= 4096:
                        raise ValueError("bad length")
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if not isinstance(body, dict) or body.get("controller_id") != ctl.controller_id:
                        raise ValueError("wrong controller")
                    if self.path in ("/status", "/live"):
                        res, code = ctl.status(), 200
                    elif self.path == "/stop":
                        res, code = ctl.request_stop("clawmetry_stop"), 200
                    elif self.path == "/reset" and body.get("acknowledge") is True:
                        res, code = ctl.reset(body.get("expected_stop_generation")), 200
                    elif self.path == "/task":
                        res, code = ctl.request_task(body.get("text"), body.get("requested_by")), 200
                    else:
                        raise ValueError(f"unsupported operation {self.path}")
                except (ValueError, RuntimeError) as e:
                    res, code = {**ctl.status(), "request_rejected": True, "message": str(e)[:250]}, 409
                raw = json.dumps(res, allow_nan=False, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True, name="clawmetry-controller"
        )
        reg_dir = _private_dir(
            Path(os.environ.get("CLAWMETRY_ROBOTICS_CONTROL_DIR") or _base() / "control")
            if directory is None
            else directory
        )
        self._path = reg_dir / f"{self.controller_id}.json"
        try:
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(
                    {
                        "controller_id": self.controller_id,
                        "port": self._server.server_port,
                        "token": self._token,
                        "metadata": self._public_status(),
                    },
                    f,
                )
        except BaseException:
            self._server.server_close()
            raise
        self._thread.start()
        skills.sinks.append(self)
        if self.journal:
            self.journal.emit(
                "run.started",
                role="skills_api",
                robot_type=self.robot_type,
                source_kind="leukerbad_skills_api",
                controller_session_id=self.controller_id,
                controller_id=self.controller_id,
                complete_control_trace=False,
                body_unit="m",
                task_requests=True,
                camera_recording=self._camera_recording(),
            )
        self._observe_period = 1.0 / observe_hz if observe_hz > 0 else 0
        self._obs_stop = threading.Event()
        self._obs = threading.Thread(target=self._observe_loop, daemon=True, name="clawmetry-observe")
        if self._observe_period:
            self._obs.start()
        log.info(
            "registered controller %s on 127.0.0.1:%d (robot %s); journal %s",
            self.controller_id,
            self._server.server_port,
            self.robot_id,
            self.journal.path if self.journal else "off",
        )

    # ------------------------------------------------------------------ status
    def _public_status(self) -> dict:
        return {
            "controller_id": self.controller_id,
            "run_id": self.journal.run_id if self.journal else None,
            "arm_id": self.robot_id,
            "robot_id": self.robot_id,
            "role": "skills_api",
            "control_mode": "skills",
            "adapter": getattr(self.skills.bridge, "stop_adapter", "bridge.stop_and_settle"),
            "capabilities": {
                "command_inhibit": True,
                # False for bodies whose stop is a task cancel (AgiBot A2: ActionCancel), not a physical halt
                "physical_stop": bool(getattr(self.skills.bridge, "physical_stop", True)),
                "reset": True,
                "manual_jog": False,
                "manual_drive": False,
                "task_request": True,
            },
            "task": self._task_public(),
        }

    def _stationary(self):
        s = self.skills.bridge.state()
        return (s.balance_ok and s.mode in STATIONARY_MODES), s

    def status(self) -> dict:
        with self._lock:
            latched = self.skills._pause.is_set()
            st = dict(self._status)
            if latched and st["state"] in ("confirmed", "unconfirmed"):
                # re-confirm from fresh bridge state on every poll (ClawMetry treats >1 s old evidence as stale)
                ok, s = self._stationary()
                st.update(
                    state="confirmed" if ok else "unconfirmed",
                    physical_stop_confirmed=ok,
                    confirmation_at_ns=time.time_ns() if ok else None,
                    message=(
                        "Stopped: bridge reports a stationary, balanced body."
                        if ok
                        else f"Stop requested; body reports mode={s.mode} balance_ok={s.balance_ok}."
                    ),
                )
            elif not latched and st["state"] not in ("requested",):
                st.update(state="ready", physical_stop_confirmed=False, message=st.get("message") or "Ready")
            return {
                **self._public_status(),
                **st,
                "latched": latched,
                "available": not self._closed,
                "stop_generation": self.generation,
                "observation_only": False,
                "manual": {"state": "unavailable", "available": False},
                "drive": {"supported": False},
                "cameras": [],
                "remote_enabled": False,
                "remote": {},
            }

    # -------------------------------------------------------------------- stop
    def request_stop(self, reason: str) -> dict:
        with self._lock:
            self.generation += 1
            gen = self.generation
            self.skills._pause.set()  # inhibit new skill commands immediately (running skills see it too)
            self._status = {
                "state": "requested",
                "physical_stop_confirmed": False,
                "requested_at_ns": time.time_ns(),
                "message": "Stop requested: bridge.stop_and_settle running.",
            }
            log.info("STOP requested by ClawMetry (generation %d): calling bridge.stop_and_settle", gen)
            self._event("control.stop_requested", stop_generation=gen, reason=reason)
            self._task_cancel.set()  # the agent process ends; the robot is stopped below, not by the agent
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._do_stop, args=(gen, reason), daemon=True, name="clawmetry-stop"
                )
                self._worker.start()
        return self.status()

    def _do_stop(self, gen: int, reason: str):
        t0 = time.time()
        try:
            saf = getattr(self.skills, "safety", None)
            if saf is not None:  # D1W edge: Stop = stop + hold (FSM HOLD until Reset); physical stop: yes
                saf.operator_stop(reason)
            r = self.skills.pause(reason, stop=True)  # pause latch + bridge.stop_and_settle() (A2: ActionCancel)
            ok, s = self._stationary()
            with self._lock:
                self._status = {
                    "state": "confirmed" if ok else "unconfirmed",
                    "physical_stop_confirmed": ok,
                    "confirmation_at_ns": time.time_ns() if ok else None,
                    "message": r.message,
                }
            log.info(
                "STOP generation %d done in %.2f s: stationary=%s mode=%s zone=%s",
                gen,
                time.time() - t0,
                ok,
                s.mode,
                s.zone,
            )
            self._event(
                "control.stop_result",
                stop_generation=gen,
                state=self._status["state"],
                physical_stop_confirmed=ok,
                evidence={
                    "observed_at_ns": time.time_ns(),
                    "source": "bridge.stop_and_settle",
                    "state": s.dict(),
                    "settle_s": round(time.time() - t0, 3),
                },
            )
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._status = {
                    "state": "failed",
                    "physical_stop_confirmed": False,
                    "message": f"stop_and_settle raised {e.__class__.__name__}: {e}"[:250],
                }
            log.error("STOP generation %d FAILED: %s", gen, e)
            self._event(
                "control.stop_result",
                stop_generation=gen,
                state="failed",
                physical_stop_confirmed=False,
                evidence={"error": str(e)[:200]},
            )

    def reset(self, expected_generation) -> dict:
        with self._lock:
            if type(expected_generation) is not int or expected_generation != self.generation:
                raise ValueError("Reset requires the current stop generation; review the latest Stop first.")
            if self._worker is not None and self._worker.is_alive():
                raise RuntimeError("Stop is still settling; reset after it completes.")
            r = self.skills.resume()
            saf = getattr(self.skills, "safety", None)
            msg = "Reset: skill commands allowed again (the robot was not moved)."
            if saf is not None:
                # D1W edge: Reset = recover into the go zone (bounded, 0.15 m/s backwards) + re-arm; a policy hold
                # then waits for Approve (ClawMetry approvals queue or the console) before NORMAL
                rr = saf.reset("ClawMetry")
                msg = (
                    "Reset: recovering into the go zone, then waiting for Approve (policy hold)."
                    if rr.get("latched")
                    else "Reset: re-armed (recovery into the go zone first if the robot was outside it)."
                )
            self._status = {
                "state": "ready",
                "physical_stop_confirmed": False,
                "message": msg,
            }
            log.info("RESET by ClawMetry (generation %d): %s", self.generation, r.message)
            self._event("control.reset", stop_generation=self.generation)
        return self.status()

    # ------------------------------------------------------------------ traces
    def _event(self, kind: str, **data):
        if self.journal:
            try:
                self.journal.emit(kind, controller_id=self.controller_id, **data)
            except Exception as e:  # noqa: BLE001  (a trace write must never block the stop path)
                log.warning("journal write failed: %s", e)

    # -------------------------------------------------------- robot sessions
    def _ids(self) -> dict:
        out = {}
        if self._session:
            out["session_id"] = self._session["id"]
        if self._turn:
            out["turn"] = self._turn["n"]
            out["turn_id"] = self._turn["id"]
        t = self._task
        if t and t["state"] == "running":
            out["task_id"] = t["id"]
        return out

    def _open_session(self, skill: str):
        sid = uuid.uuid4().hex[:16]
        self._session = {"id": sid, "started": time.time(), "turns": 0, "outcomes": {}}
        self._event(
            "session.started",
            session_id=sid,
            opened_by=skill,
            idle_close_s=SESSION_IDLE_S,
            policy=self.skills.policy.name,
            policy_version=self.skills.policy.raw.get("version"),
        )

    def _close_session(self, reason: str):
        se, self._session = self._session, None
        if se:
            self._event(
                "session.ended",
                session_id=se["id"],
                reason=reason,
                turns=se["turns"],
                outcomes=se["outcomes"],
                duration_s=round(time.time() - se["started"], 3),
            )

    def _thumbnail(self, n: int) -> str | None:
        if not self.thumbnails or self.journal is None:
            return None
        d = self.journal.path.parent.parent / "thumbs" / self.journal.run_id
        try:
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = d / f"{self._session['id']}_{n:03d}.jpg"
            self.skills.bridge.screenshot(path, camera="demo_cam", width=THUMB_SIZE[0], height=THUMB_SIZE[1])
            return str(path) if path.exists() else None
        except Exception as e:  # noqa: BLE001  (a missing thumbnail never blocks a skill)
            log.warning("thumbnail failed: %s", e)
            return None

    def turn_begin(self, skill: str, args: dict, rationale: str | None, s0) -> None:
        with self._slock:
            now = time.time()
            if self._session and now - self._last_activity > SESSION_IDLE_S:
                self._close_session("idle")
            if self._session is None:
                self._open_session(skill)
            self._session["turns"] += 1
            n = self._session["turns"]
            self._turn = {"n": n, "id": f"{self._session['id']}-{n}", "t0": now, "x0": (s0.x, s0.y)}
            self._last_activity = now
            snap = {
                "x": round(s0.x, 3),
                "y": round(s0.y, 3),
                "yaw": round(s0.yaw, 3),
                "zone": s0.zone,
                "battery_pct": round(s0.battery_pct, 2),
                "balance_ok": s0.balance_ok,
                "holding": s0.holding,
                "mode": s0.mode,
                "arm_q": [round(v, 3) for v in s0.arm_q],
            }
            self._event("turn.observe", **self._ids(), skill=skill, snapshot=snap, thumbnail=self._thumbnail(n))
            self._event("turn.decide", **self._ids(), tool=skill, args=args, rationale=rationale)

    def event(self, kind: str, data: dict) -> None:
        with self._slock:
            self._event(KIND.get(kind, kind), **self._ids(), **data)

    def turn_end(self, skill: str, r, outcome: str, dev: dict | None, s_end, tr: dict | None = None) -> None:
        with self._slock:
            if self._turn is None:
                return
            d = r.dict()
            x0, y0 = self._turn["x0"]
            path = d.get("path_len_m")
            path_src = "skill"
            if path is None and tr and tr.get("path_m") is not None:
                path, path_src = round(tr["path_m"], 3), "monitor"
            if path is None:
                path, path_src = round(math.hypot(s_end.x - x0, s_end.y - y0), 3), "displacement"
            self._event(
                "turn.feedback",
                **self._ids(),
                skill=skill,
                confirmed_state=d.get("confirmed_state"),
                duration_ms=d.get("duration_ms"),
                path_len_m=path,
                path_source=path_src,
                deviation=None if dev is None else {k: dev[k] for k in ("position_error_m", "duration_error_s")},
            )
            self._event(
                "turn.outcome", **self._ids(), skill=skill, outcome=outcome, ok=bool(r.ok), message=r.message[:300]
            )
            # mirror into the kinds the page counts (blocked / error badges)
            if outcome == "blocked_by_policy":
                self._event("action.blocked", **self._ids(), skill=skill, message=r.message[:300])
            elif outcome == "failed":
                self._event("robot.error", **self._ids(), skill=skill, message=r.message[:300])
            se = self._session
            se["outcomes"][outcome] = se["outcomes"].get(outcome, 0) + 1
            self._turn = None
            self._last_activity = time.time()
            if skill == "place" and r.ok:
                self._close_session("place")
            elif skill == "hold_pose" and "handover" in r.message.lower():
                self._close_session("handover")

    def check_idle(self):
        with self._slock:
            if self._session and self._turn is None and time.time() - self._last_activity > SESSION_IDLE_S:
                self._close_session("idle")

    def _observe_loop(self):
        while not self._obs_stop.wait(self._observe_period):
            try:
                self.check_idle()
                s = self.skills.bridge.state()
                self._event(
                    "observation",
                    state=s.dict(),
                    paused=self.skills._pause.is_set(),
                    base={"position_m": [s.x, s.y, 0.0], "yaw_rad": s.yaw},
                )
            except Exception as e:  # noqa: BLE001
                log.warning("observation failed: %s", e)
            self._maybe_frame()

    # ---------------------------------------------------------- replay frames
    def _camera_recording(self) -> dict:
        if not self._frame_period:
            return {"enabled": False}
        return {
            "enabled": True,
            "max_fps_per_camera": round(1.0 / self._frame_period, 3),
            "max_width": FRAME_SIZE[0],
            "max_height": FRAME_SIZE[1],
            "max_frame_bytes": FRAME_MAX_BYTES,
            "max_total_jpeg_bytes": FRAME_BUDGET_BYTES,
            "camera": "demo_cam",
        }

    def _maybe_frame(self, force: bool = False) -> bool:
        """Journal one demo_cam JPEG (camera.frame) at most every 1/frames_hz seconds, within the per-run byte budget.
        Pro's Physical AI page shows the latest frame at the selected moment and replays them with 'Play session'."""
        if not self._frame_period or self.journal is None or self._frames_full:
            return False
        now = time.monotonic()
        if not force and now < self._frame_next:
            return False
        self._frame_next = now + self._frame_period
        d = self.journal.path.parent.parent / "thumbs" / self.journal.run_id / "frames"
        path = d / f"{self._frame_count + 1:06d}.jpg"
        try:
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
            observed_ns = time.time_ns()
            self.skills.bridge.screenshot(path, camera="demo_cam", width=FRAME_SIZE[0], height=FRAME_SIZE[1])
            data = path.read_bytes()
            path.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001  (a missing frame never blocks observation or a skill)
            log.warning("frame failed: %s", e)
            return False
        if not data.startswith(b"\xff\xd8") or len(data) > FRAME_MAX_BYTES:
            log.warning("frame skipped: %d bytes (limit %d) or not JPEG", len(data), FRAME_MAX_BYTES)
            return False
        if self._frame_bytes + len(data) > FRAME_BUDGET_BYTES:
            self._frames_full = True
            self._event(
                "camera.status",
                camera="demo_cam",
                recorded_frames=self._frame_count,
                dropped_frames=1,
                error="Camera recording reached its size limit. Observation and skill tracing continue.",
            )
            return False
        self._frame_count += 1
        self._frame_bytes += len(data)
        self._event(
            "camera.frame",
            camera="demo_cam",
            mime_type="image/jpeg",
            jpeg_base64=base64.b64encode(data).decode("ascii"),
            width=FRAME_SIZE[0],
            height=FRAME_SIZE[1],
            observed_wall_time_ns=str(observed_ns),
            **self._ids(),
        )
        return True

    # ------------------------------------------------- requests from ClawMetry
    def _task_public(self) -> dict | None:
        t = self._task
        if not t:
            return None
        return {
            k: t[k]
            for k in ("id", "state", "text", "requested_by", "started_at_ns", "intent", "summary", "outcome", "agent")
            if k in t
        }

    def request_task(self, text, requested_by) -> dict:
        """A guest request typed into ClawMetry: journal who asked and what, then run the agent against the skills.
        The agent only ever acts through the seven skills, so every policy, the gate and the monitor still apply."""
        if not isinstance(text, str) or not text.strip() or len(text) > TASK_TEXT_MAX:
            raise ValueError(f"text must be 1 to {TASK_TEXT_MAX} characters")
        who = requested_by if isinstance(requested_by, str) and requested_by.strip() else "operator"
        if len(who) > TASK_WHO_MAX:
            raise ValueError(f"requested_by must be at most {TASK_WHO_MAX} characters")
        with self._lock:
            if self._closed:
                raise RuntimeError("controller closed")
            if self.skills._pause.is_set():
                raise RuntimeError("Stop is latched; reset the controller before a new request")
            if self._task and self._task["state"] == "running":
                raise RuntimeError("a request is already running; wait for it or Stop")
            agent = self._agent or __import__("skills.agent_runner", fromlist=["run"])
            task = {
                "id": uuid.uuid4().hex[:16],
                "state": "running",
                "text": text.strip(),
                "requested_by": who.strip(),
                "started": time.time(),
                "started_at_ns": time.time_ns(),
                "agent": agent.describe(),
            }
            self._task = task
            self._task_cancel = threading.Event()
            self._event(
                "task.requested",
                task_id=task["id"],
                text=task["text"],
                requested_by=task["requested_by"],
                source="clawmetry_console",
                agent=task["agent"],
            )
            self._task_worker = threading.Thread(
                target=self._run_task, args=(task, agent, self._task_cancel), daemon=True, name="clawmetry-task"
            )
            self._task_worker.start()
        return self.status()

    def _run_task(self, task: dict, agent, cancel: threading.Event):
        def intent(text):
            task["intent"] = text
            self._event("task.intent", task_id=task["id"], text=text)

        def tool(name, args):
            self._event("task.step", task_id=task["id"], tool=name, args=args)

        try:
            r = agent.run(task["text"], task["requested_by"], on_intent=intent, on_tool=tool, cancel=cancel)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "outcome": "failed", "summary": "", "error": f"{e.__class__.__name__}: {e}"[:300]}
        with self._lock:
            task.update(
                state="done" if r.get("ok") else "failed",
                outcome=r.get("outcome", "failed"),
                summary=r.get("summary", ""),
                error=r.get("error"),
                duration_s=round(time.time() - task["started"], 3),
            )
        self._event(
            "task.completed",
            task_id=task["id"],
            ok=bool(r.get("ok")),
            outcome=task["outcome"],
            summary=task["summary"],
            error=task["error"],
            duration_s=task["duration_s"],
            turns=r.get("turns"),
            cost_usd=r.get("cost_usd"),
        )
        log.info("task %s %s in %.1f s: %s", task["id"], task["outcome"], task["duration_s"], task["summary"][:120])

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self in self.skills.sinks:
            self.skills.sinks.remove(self)
        self._task_cancel.set()
        with self._slock:
            self._close_session("shutdown")
        self._obs_stop.set()
        self._path.unlink(missing_ok=True)
        self._server.shutdown()
        self._server.server_close()
        self._event("run.ended", reason="skills api shutdown")
        if self.journal:
            self.journal.close()
        log.info("controller %s unregistered", self.controller_id)


def start(skills) -> SkillsController | None:
    """Register unless CLAWMETRY_CONTROLLER=0. Never raises: the Digital AI (OTLP) path must not depend on this."""
    if os.environ.get("CLAWMETRY_CONTROLLER", "1") == "0":
        return None
    try:
        return SkillsController(skills)
    except Exception as e:  # noqa: BLE001
        log.warning("live controller not registered: %s", e)
        return None
