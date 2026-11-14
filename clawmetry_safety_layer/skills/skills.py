"""The seven skills. Bounded, policy-checked, evidence-returning.

This is the ONLY place the agent can touch the robot. Zone policy is
enforced here regardless of what ClawMetry's approval gate does upstream.
"""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import asdict, dataclass, field

from bridge import ROOT, load_config, make_bridge
from bridge.base import (
    ARM_CLOSE,
    ARM_LIFT,
    ARM_REACH,
    ARM_REST,
    ARM_TRAY_HOLD,
    GRASP_FWD,
    GRASP_Z,
    HARDWARE_BODIES,
    SURFACE_POSE,
    RobotState,
)
from bridge.predictor import GotoPlan
from bridge.zones import Zones
from safety.fsm import SafetyHold
from skills.behaviour import BehaviourPolicy
from skills.gate import CommandGate, PredictedBreach
from telemetry import otel

# docking (approach) and grasp choreography
APPROACH_MAX_S = 8.0  # whole approach is bounded
APPROACH_MAX_M = 1.6  # farther than this from the target: navigate_to its zone first
APPROACH_TOL_M = 0.01  # stop when the pelvis is this close to the stand-off distance
GRASP_TOL_M = 0.05  # hands' grasp site must be this close to the prop's grasp point
# arm timing (identical on every body, so the real G1 gets the same choreography and durations as the sim)
REACH_S, CLOSE_S, REST_S = 1.5, 0.4, 1.5
LIFT_UP_S, LIFT_CARRY_S = 0.5, 1.0  # pick:  close -> lift (straight up ~10 cm) -> hold   (1.5 s)
OVER_S, DOWN_S = 0.75, 0.75  # place: hold -> above slot -> down onto slot         (1.5 s)


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


@dataclass
class SkillResult:
    ok: bool
    skill: str
    message: str = ""
    confirmed_state: dict = field(default_factory=dict)
    duration_ms: float = 0.0
    extra: dict = field(default_factory=dict)

    def dict(self) -> dict:
        d = asdict(self)
        d.update(d.pop("extra"))
        d["duration_ms"] = round(d["duration_ms"], 1)
        return d


class Paused(Exception):
    pass


class MonitorStop(Exception):
    """The continuous monitor stopped the body during this skill (hard breach)."""


LIFT_DZ = 0.10  # LIFT poses are the CLOSE poses ~10 cm straight up (scripts/solve_arm_poses.py)


class Skills:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        self.bridge = make_bridge(self.cfg)
        # arm poses / docking distances: from the body when it provides them (vendor bodies), else the G1 set
        self.P = getattr(self.bridge, "arm_poses", None) or {
            "REST": ARM_REST,
            "HOLD": ARM_TRAY_HOLD,
            "REACH": ARM_REACH,
            "CLOSE": ARM_CLOSE,
            "LIFT": ARM_LIFT,
            "GRASP_FWD": GRASP_FWD,
        }
        self.zones = Zones(ROOT / self.cfg.get("zones_file", "policies/zones.yaml"))
        # behaviour policy (policies/behaviour.yaml): speed caps, allowed objects/targets, skill bounds, forbidden
        # actions, quiet hours. Enforced here, by the command gate's predictor and by the API's monitor.
        self.policy = BehaviourPolicy(self.cfg.get("behaviour_file"), self.zones)
        # time_compression: a fake/sim body that runs N x faster than real time (tests use speed_scale 8 to finish
        # in seconds). Speeds are judged against the policy in real-robot time (observed / N). Never on hardware.
        self.time_compression = float(self.cfg.get("time_compression", 1.0))
        if self.time_compression != 1.0 and self.bridge.body in HARDWARE_BODIES:
            raise ValueError("time_compression is only for fake/sim bodies")
        self.gate = CommandGate(self)
        self.escort_pending: dict | None = None  # a staff-escort request waiting for Approve / Deny
        self.safety = None  # safety/supervisor.py attaches itself (D1W edge); None = the pre-FSM behaviour
        self.bridge.event_sink = self.emit  # bridge-side events (e.g. observe-only refusals) join the trace
        self.sinks: list = []  # trace sinks (skills/clawmetry_robotics.py): turn_begin / event / turn_end
        self._turn: dict | None = None
        self._abort = threading.Event()
        self._abort_reason = ""
        self._carrying: str | None = None
        self._fail_streak: dict = {}
        self._tl = threading.local()
        self.nav_tol = float(self.cfg.get("nav_tolerance_m", 0.3))
        self.nav_max_s = float(self.cfg.get("nav_max_s", 20))
        self.approach_yaw_rate = float(self.cfg.get("approach_yaw_rate", 0.6))  # rad/s cap while docking
        self._pause = threading.Event()
        self._fail_pick_left = int(os.environ.get("FAIL_PICK_N", "0"))
        self._consecutive_fail = 0
        self._lock = threading.Lock()
        os.environ.setdefault("ROBOT_BODY", self.bridge.body)
        otel.init()

    # ------------------------------------------------------------ pause/resume
    def pause(self, reason: str = "operator", stop: bool = False) -> SkillResult:
        """Latch the pause and stop the body. Task-level bodies (AgiBot A2) pause their running task instead
        (ActionPause) unless stop=True (ClawMetry Stop), which cancels it (ActionCancel, NOT a physical halt)."""
        self._pause.set()
        if not stop and getattr(self.bridge, "task_level", False):
            paused = self.bridge.pause_task()
            s = self.bridge.state()
            return SkillResult(True, "pause", f"paused: {reason}" + (" (task paused)" if paused else ""), s.dict())
        s = self.bridge.stop_and_settle()
        return SkillResult(True, "pause", f"paused: {reason}", s.dict())

    def resume(self) -> SkillResult:
        self._pause.clear()
        if getattr(self.bridge, "task_level", False):
            self.bridge.resume_task()
        return SkillResult(True, "resume", "resumed", self.bridge.state().dict())

    def _check_pause(self):
        if self._pause.is_set():
            raise Paused()
        if self._abort.is_set():
            raise MonitorStop(self._abort_reason)

    # ------------------------------------------------------------ trace sinks
    def emit(self, kind: str, **data):
        for sink in list(self.sinks):
            try:
                sink.event(kind, data)
            except Exception as e:  # noqa: BLE001  (a trace sink must never break a skill)
                print(f"[skills] trace sink failed on {kind}: {e}")

    def _sinks(self, method: str, *args):
        for sink in list(self.sinks):
            try:
                getattr(sink, method)(*args)
            except Exception as e:  # noqa: BLE001
                print(f"[skills] trace sink {method} failed: {e}")

    def set_rationale(self, text: str | None):
        """The agent's one-line reason for the next skill call on this thread (recorded in the decide event)."""
        self._tl.rationale = (text or "").strip()[:300] or None

    def monitor_stop(self, reason: str):
        """Called by the continuous monitor on a hard breach (after it has called stop_and_settle): the running
        skill, if any, stops issuing commands and returns failed."""
        self._abort_reason = reason
        self._abort.set()

    # ------------------------------------------------- turn bookkeeping (gate)
    def _turn_predicted(self, pred, command: dict):
        tr = self._turn
        if tr is None or tr.get("plan"):
            return
        tr["pred_end"] = pred.end
        tr["predictions"] += 1

    def _turn_arm_done(self, ramp_s: float, actual_s: float):
        tr = self._turn
        if tr is not None:
            tr["arm_pred_s"] += ramp_s
            tr["arm_actual_s"] += actual_s

    def _deviation(self, tr: dict, s_end: RobotState, duration_s: float) -> dict | None:
        """Predicted vs actual for this turn. navigate_to: the whole goto plan (end pose, duration). Other skills:
        the arm ramps (predicted ramp vs measured) and the end pose of the last prediction."""
        if self.gate.commands_sent == tr["cmd0"]:
            return None  # nothing was executed (blocked / refused before any command)
        if tr.get("plan"):
            pe, pd, scope = tr["pred_end"], tr["pred_duration"], "goto_plan"
            dur_err = duration_s - pd
            predicted_s, actual_s = pd, duration_s
        elif tr["predictions"]:
            pe, scope = tr["pred_end"], "commands"
            predicted_s, actual_s = tr["arm_pred_s"], tr["arm_actual_s"]
            dur_err = actual_s - predicted_s
        else:
            return None
        pos_err = math.hypot(s_end.x - pe[0], s_end.y - pe[1]) if pe else 0.0
        yaw_err = abs(_wrap(s_end.yaw - pe[2])) if pe else 0.0
        return {
            "scope": scope,
            "position_error_m": round(pos_err, 3),
            "yaw_error_rad": round(yaw_err, 3),
            "duration_error_s": round(dur_err, 3),
            "predicted_duration_s": round(predicted_s, 3),
            "actual_duration_s": round(actual_s, 3),
            "predicted_end": [round(v, 3) for v in pe] if pe else None,
            "actual_end": [round(s_end.x, 3), round(s_end.y, 3), round(s_end.yaw, 3)],
        }

    def _hold(self, reason: str) -> RobotState:
        """hold_pose's body (callable inside another skill): stop and settle in place; never damps."""
        s = self.gate.stop_and_settle()
        self.emit("command", type="hold_pose", reason=reason[:200])
        return s

    @staticmethod
    def _outcome(r: SkillResult) -> str:
        if r.ok:
            return "ok"
        if r.message.startswith("blocked_by_policy") or r.extra.get("blocked_by_policy"):
            return "blocked_by_policy"
        if "paused" in r.message:
            return "paused"
        return "failed"

    def _policy_precheck(self, name: str, attrs: dict) -> SkillResult | None:
        why = self.policy.skill_forbidden(name)
        if why:
            return SkillResult(
                False, name, f"blocked_by_policy: {why}", self.bridge.state().dict(), extra={"blocked_by_policy": True}
            )
        key = (name, tuple(sorted((k, str(v)) for k, v in attrs.items())))
        if self._fail_streak.get(key, 0) > self.policy.max_retries:
            return SkillResult(
                False,
                name,
                f"blocked_by_policy: {name} already failed {self._fail_streak[key]} times "
                f"in a row with these arguments (max_retries {self.policy.max_retries})",
                self.bridge.state().dict(),
                extra={"blocked_by_policy": True, "retry_limit": True},
            )
        return None

    def _guard(self, name: str, fn, **attrs) -> SkillResult:
        """Run a skill under a lock, a span, pause/monitor handling, the behaviour policy and the turn trace."""
        rationale = getattr(self._tl, "rationale", None)
        self._tl.rationale = None
        with self._lock:
            t0 = time.time()
            self._abort.clear()
            s0 = self.bridge.state()
            tr = self._turn = {
                "skill": name,
                "plan": False,
                "pred_end": None,
                "pred_duration": None,
                "predictions": 0,
                "arm_pred_s": 0.0,
                "arm_actual_s": 0.0,
                "cmd0": self.gate.commands_sent,
            }
            self._sinks("turn_begin", name, dict(attrs), rationale, s0)
            with otel.skill_span(name, **attrs) as out:
                try:
                    self._check_pause()
                    held = self.safety.check_skill(name) if self.safety is not None else None
                    if held:
                        raise SafetyHold(held)
                    r: SkillResult = self._policy_precheck(name, attrs) or fn()
                except Paused:
                    s = self.bridge.state()
                    r = SkillResult(False, name, "robot is paused by operator; wait for resume", s.dict())
                except PredictedBreach as e:
                    s = self._hold(f"predicted breach: {e}")
                    r = SkillResult(
                        False,
                        name,
                        f"blocked_by_policy: predicted breach: {e}",
                        s.dict(),
                        extra={"blocked_by_policy": True, "breach_predicted": e.breaches},
                    )
                except SafetyHold as e:
                    s = self.gate.stop_and_settle()
                    r = SkillResult(
                        False,
                        name,
                        f"held by safety: {e}",
                        s.dict(),
                        extra={"safety": self.safety.snapshot() if self.safety else None},
                    )
                except MonitorStop as e:
                    s = self.gate.stop_and_settle()
                    held = self.safety is not None and self.safety.fsm.state in ("HOLD", "RECOVER")
                    r = SkillResult(
                        False,
                        name,
                        f"{'held by safety' if held else 'stopped by monitor'}: {e}",
                        s.dict(),
                        extra={
                            "monitor_stop": True,
                            **({"safety": self.safety.snapshot()} if held and self.safety else {}),
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    s = self.gate.stop_and_settle()
                    r = SkillResult(False, name, f"error: {e.__class__.__name__}: {e}", s.dict())
                self.gate._close_segment()
                r.duration_ms = (time.time() - t0) * 1000
                lim = self.policy.skill_limit(name, "max_duration_s")
                if lim is not None and r.duration_ms > lim * 1000 / self.time_compression:
                    r.extra["over_max_duration"] = True
                    self.emit(
                        "breach",
                        rule="max_duration",
                        severity="soft",
                        skill=name,
                        value=round(r.duration_ms / 1000, 3),
                        cap=lim,
                        source="skills",
                        detail=f"{name} took {r.duration_ms / 1000:.1f} s (max {lim:.0f} s)",
                    )
                s_end = self.bridge.state()
                dev = self._deviation(tr, s_end, r.duration_ms / 1000)
                if dev is not None:
                    self.emit("deviation", skill=name, **dev)
                    out.update(
                        {
                            "deviation_pos_m": dev["position_error_m"],
                            "deviation_dur_s": dev["duration_error_s"],
                            "deviation_pred_s": dev["predicted_duration_s"],
                        }
                    )
                    r.extra["deviation"] = {k: dev[k] for k in ("position_error_m", "duration_error_s")}
                outcome = self._outcome(r)
                out.update(
                    {
                        "ok": r.ok,
                        "zone": r.confirmed_state.get("zone", ""),
                        "message": r.message[:120],
                        "outcome": outcome,
                    }
                )
                self._push_gauges(s_end)
                # A state read is observational: it must not erase a failed action from the
                # operator/agent-visible streak. Successful action skills start a fresh streak.
                if name != "get_state":
                    self._consecutive_fail = 0 if r.ok else self._consecutive_fail + 1
                r.extra["consecutive_failures"] = self._consecutive_fail
                key = (name, tuple(sorted((k, str(v)) for k, v in attrs.items())))
                if outcome == "failed":
                    self._fail_streak[key] = self._fail_streak.get(key, 0) + 1
                elif outcome == "ok":
                    self._fail_streak.pop(key, None)
                self._turn = None
                self._sinks("turn_end", name, r, outcome, dev, s_end, tr)
                return r

    # ----------------------------------------------------------- arm heights
    def _pose_table(self) -> list[tuple[str, list[float], float | None]]:
        """(name, q, grasp-site height) for every known arm pose (heights from the IK solve: bridge.base.GRASP_Z;
        vendor bodies may give GRASP_Z in their poses file)."""
        gz = self.P.get("GRASP_Z") or GRASP_Z
        rows = [("REST", self.P["REST"], None), ("HOLD", self.P["HOLD"], gz.get("HOLD"))]
        for fam in ("SHELF", "TABLE"):
            z = gz.get(fam)
            for nm, d, dz in (("REACH", "REACH", 0.0), ("CLOSE", "CLOSE", 0.0), ("LIFT", "LIFT", LIFT_DZ)):
                if fam in (self.P.get(d) or {}):
                    rows.append((f"{nm}_{fam}", self.P[d][fam], None if z is None else z + dz))
        for nm in ("HOVER", "LOW"):
            for reg, q in (self.P.get(nm) or {}).items():
                rows.append((f"{nm}_{reg}", q, (gz.get(nm) or {}).get(reg) if isinstance(gz.get(nm), dict) else None))
        return rows

    def _nearest_pose(self, q):
        best, bd = None, 1e9
        for row in self._pose_table():
            if len(row[1]) != len(q):
                continue
            d = math.sqrt(sum((a - b) ** 2 for a, b in zip(row[1], q, strict=False)))
            if d < bd:
                best, bd = row, d
        return best, bd

    def pose_name(self, q) -> str | None:
        row, d = self._nearest_pose(list(q))
        return row[0] if row is not None and d < 1e-3 else None

    def hold_height(self, q) -> float | None:
        """Grasp-site height (m) for an arm configuration: exact for the solved poses, nearest solved pose within
        0.3 rad otherwise (mid-ramp), None when unknown."""
        row, d = self._nearest_pose(list(q))
        return row[2] if row is not None and d < 0.3 else None

    def _push_gauges(self, s: RobotState):
        otel.gauge("robot_battery_pct", s.battery_pct)
        otel.gauge("robot_balance_ok", 1.0 if s.balance_ok else 0.0)
        otel.gauge("robot_zone_idx", float(self.zones.names().index(s.zone)) if s.zone in self.zones.names() else -1)

    # ----------------------------------------------------------------- skills
    def get_state(self) -> SkillResult:
        def run():
            s = self.bridge.state()
            extra = {"zones": self.policy.describe_zones(), "paused": self._pause.is_set(), "policy": self.policy.name}
            # Report object locations only when the bridge has real perception for them.
            # Hardware bridges without perception keep this field absent rather than implying
            # that props are at their configured home locations.
            object_pose = getattr(self.bridge, "object_pose", None)
            prop_support = getattr(self.bridge, "prop_support", None)
            if callable(object_pose):
                objects = {}
                for obj in self.policy.allowed_objects:
                    pose = object_pose(obj)
                    if pose is None:
                        continue
                    position = pose[0] if isinstance(pose, (tuple, list)) else pose
                    try:
                        xyz = [float(position[i]) for i in range(3)]
                    except (IndexError, TypeError, ValueError):
                        continue
                    observed = {"position_m": xyz, "zone": self.zones.zone_of(xyz[0], xyz[1])}
                    if callable(prop_support):
                        support = prop_support(obj)
                        if support is not None:
                            observed["support"] = support
                    objects[obj] = observed
                if objects:
                    extra["objects"] = objects
            if os.environ.get("FORCE_ZONE_C") == "1":
                extra["pending_request"] = "Guest asked: take the tray to the pool deck (zone C)."
            return SkillResult(True, "get_state", "ok", s.dict(), extra=extra)

        return self._guard("get_state", run)

    def navigate_to(self, zone: str, max_s: float | None = None) -> SkillResult:
        max_s = float(max_s or self.nav_max_s)

        def run():
            if zone not in self.zones.names():
                return SkillResult(
                    False,
                    "navigate_to",
                    f"unknown zone {zone!r}; known: {self.zones.names()}",
                    self.bridge.state().dict(),
                )
            s = self.bridge.state()
            tx, ty = self.zones.centre(zone)
            table = getattr(self.bridge, "table_body", False)
            limit_s = min(max_s, self.policy.skill_limit("navigate_to", "max_duration_s") or max_s)
            max_path = self.policy.skill_limit("navigate_to", "max_path_m")
            holding = bool(s.holding)
            if not table:
                # predict the whole walk (navigate_to's own steering law) before any command is sent
                near = bool(getattr(self.bridge, "near_person", lambda: False)())
                cap_of = lambda z: self.policy.speed_cap(z, holding, near)  # noqa: E731
                pred = self.gate.predictor.predict_goto(
                    s,
                    GotoPlan((tx, ty), self.nav_tol, limit_s),
                    self.policy.horizon_s,
                    self.policy.dt_s,
                    self.zones.zone_of,
                    cap_of,
                    settle_s=0.6,
                )
                command = {"type": "goto", "goal": [tx, ty], "zone": zone, "max_s": limit_s}
                self._turn.update(plan=True, pred_end=pred.end, pred_duration=pred.duration_s)
                self.gate._gate(pred, s, holding, self.hold_height(s.arm_q) if holding else None, near, command)
                if max_path is not None and pred.path_m is not None and pred.path_m > max_path:
                    return SkillResult(
                        False,
                        "navigate_to",
                        f"blocked_by_policy: predicted path {pred.path_m:.1f} m exceeds max_path_m {max_path}",
                        s.dict(),
                        extra={"blocked_by_policy": True},
                    )
            why = self.policy.forbidden_zone(zone)
            if why:
                return SkillResult(
                    False, "navigate_to", f"blocked_by_policy: {why}", s.dict(), extra={"blocked_by_policy": True}
                )
            crossed = self._path_forbidden(s.x, s.y, tx, ty)
            if crossed:
                return SkillResult(
                    False,
                    "navigate_to",
                    f"blocked_by_policy: straight path to {zone} crosses restricted zone {crossed}",
                    s.dict(),
                    extra={"blocked_by_policy": True},
                )
            if table:  # fixed-base arm: zones are regions on its table
                return self._table_move(zone)
            if getattr(self.bridge, "task_level", False):  # the robot's own planner drives (AgiBot A2)
                return self._task_navigate(zone, tx, ty, limit_s)
            if s.mode in ("idle", "damped"):
                self.bridge.stand()
            start = (s.x, s.y)
            t0 = time.time()
            path = 0.0
            last = (s.x, s.y)
            while time.time() - t0 < limit_s:
                self._check_pause()
                s = self.bridge.state()
                d = s.dist_to(tx, ty)
                path += math.hypot(s.x - last[0], s.y - last[1])
                last = (s.x, s.y)
                if d < self.nav_tol:
                    break
                if max_path is not None and path > max_path:
                    s = self.gate.stop_and_settle()
                    return SkillResult(
                        False,
                        "navigate_to",
                        f"blocked_by_policy: walked {path:.1f} m, over max_path_m {max_path}",
                        s.dict(),
                        extra={"blocked_by_policy": True, "path_len_m": round(path, 2)},
                    )
                heading = math.atan2(ty - s.y, tx - s.x)
                err = (heading - s.yaw + math.pi) % (2 * math.pi) - math.pi
                near = bool(getattr(self.bridge, "near_person", lambda: False)())
                if abs(err) > 0.35:
                    self.gate.move_velocity(0.0, 0.0, max(-0.6, min(0.6, 1.5 * err)), until_yaw=abs(err) - 0.35)
                else:
                    v = min(
                        self.policy.speed_cap(zone, holding, near),
                        self.policy.speed_cap(s.zone, holding, near) if s.zone in self.zones.names() else 9.9,
                    )
                    self.gate.move_velocity(v, 0.0, max(-0.4, min(0.4, 1.0 * err)), until_m=d - self.nav_tol)
                # refuse to walk into a restricted zone even if the plan drifts (only when driving forward: a turn in
                # place facing a restricted zone, e.g. after backing out of it, goes nowhere)
                nx, ny = s.x + 0.5 * math.cos(s.yaw), s.y + 0.5 * math.sin(s.yaw)
                if abs(err) <= 0.35 and self.policy.forbidden_zone(self.zones.zone_of(nx, ny)):
                    self.gate.stop_and_settle()
                    return SkillResult(
                        False,
                        "navigate_to",
                        "blocked_by_policy: next step would enter a restricted zone",
                        self.bridge.state().dict(),
                        extra={"blocked_by_policy": True},
                    )
                time.sleep(0.1)
            s = self.gate.stop_and_settle()
            ok = s.dist_to(tx, ty) < self.nav_tol
            return SkillResult(
                ok,
                "navigate_to",
                "arrived" if ok else f"timeout {limit_s:.0f}s, {s.dist_to(tx, ty):.2f} m short",
                s.dict(),
                extra={
                    "final_zone": s.zone,
                    "path_len_m": round(path, 2),
                    "start": [round(start[0], 2), round(start[1], 2)],
                },
            )

        return self._guard("navigate_to", run, zone=zone)

    def approach(self, target_body: str, obj: str | None = None, stand_off: float = GRASP_FWD["SHELF"]):
        """Dock in front of a prop (target_body = prop name) or a place target's slot for obj, so the solved
        reach pose puts the hands' grasp site on it.

        The docking point is stand_off from the target on the robot's side of the furniture: out along the
        outward normal of the footprint edge that faces the robot (or, with no furniture under the target,
        along the line from the target to the robot). Sequence: turn in place toward the docking point (vyaw
        only), walk straight to it, turn in place to face the target, close the last centimetres straight on,
        stop_and_settle. Bounded by APPROACH_MAX_S; target, docking point and path are checked against the
        zone policy and furniture footprints before moving. Returns None when docked (or when the body has no
        geometry, e.g. fake / real G1 without perception: the arm choreography then runs from where it
        stands), else a failed SkillResult."""
        T = self.bridge.target_point(target_body, obj)
        if T is None:
            return None
        tx, ty = float(T[0]), float(T[1])
        s = self.bridge.state()

        def fail(msg, **extra):
            return SkillResult(False, "approach", msg, self.bridge.state().dict(), extra=extra)

        n = self.bridge.approach_normal(tx, ty, s.x, s.y)
        if n is None:
            d0 = math.hypot(s.x - tx, s.y - ty)
            n = ((s.x - tx) / max(d0, 1e-6), (s.y - ty) / max(d0, 1e-6))
        gx, gy = tx + stand_off * n[0], ty + stand_off * n[1]
        for z in (self.zones.zone_of(tx, ty), self.zones.zone_of(gx, gy)):  # policy first
            if self.policy.forbidden_zone(z):
                return fail(
                    f"blocked_by_policy: {target_body} / docking point is in restricted zone {z}",
                    blocked_by_policy=True,
                )
        crossed = self._path_forbidden(s.x, s.y, gx, gy)
        if crossed:
            return fail(f"blocked_by_policy: docking path crosses restricted zone {crossed}", blocked_by_policy=True)
        travel = math.hypot(gx - s.x, gy - s.y)
        if travel > APPROACH_MAX_M:
            return fail(
                f"{target_body} is {math.hypot(tx - s.x, ty - s.y):.1f} m away "
                f"(zone {self.zones.zone_of(tx, ty)}); navigate_to that zone first",
                out_of_reach=True,
            )
        skill = self._turn["skill"] if self._turn else "pick"
        max_path = self.policy.skill_limit(skill, "max_path_m")
        if max_path is not None and travel > max_path:
            return fail(
                f"blocked_by_policy: docking walk {travel:.1f} m exceeds max_path_m {max_path} for {skill}",
                blocked_by_policy=True,
            )
        # the docking point keeps the clearance margin; the way there must not cross actual furniture
        blocker = self.bridge.footprint_blocked(gx, gy)
        if blocker:
            return fail(
                f"cannot dock at {target_body}: would have to stand inside the {blocker} footprint", out_of_reach=True
            )
        # (if a fast navigate_to overshot into a footprint, walking back out of it is allowed)
        start_in = self.bridge.footprint_blocked(s.x, s.y, 0.0)
        steps = max(1, int(travel / 0.05))
        for i in range(1, steps + 1):
            px, py = s.x + (gx - s.x) * i / steps, s.y + (gy - s.y) * i / steps
            blocker = self.bridge.footprint_blocked(px, py, 0.0)
            if blocker and blocker != start_in:
                return fail(f"cannot dock at {target_body}: the way in crosses the {blocker}", out_of_reach=True)
        t0 = time.time()

        def timed_out():
            return time.time() - t0 > APPROACH_MAX_S

        def turn_to(heading):
            while True:
                self._check_pause()
                err = _wrap(heading - self.bridge.state().yaw)
                if abs(err) < 0.02 or timed_out():
                    break
                cap = self.approach_yaw_rate
                self.gate.move_velocity(0.0, 0.0, max(-cap, min(cap, 2.5 * err)), until_yaw=abs(err))
                time.sleep(0.02)
            self.gate.stop()

        def walk_until(remaining):
            """walk straight (forward, or back a little) until remaining(state) ~ 0"""
            while True:
                self._check_pause()
                st = self.bridge.state()
                r = remaining(st)
                if abs(r) < APPROACH_TOL_M or timed_out():
                    break
                v = max(-0.1, min(0.25, 2.5 * r))
                v = math.copysign(max(abs(v), 0.04), v)  # no creeping tail: the last cm at 4 cm/s
                ahead = math.copysign(min(0.05, abs(r)), v)  # look ahead only as far as still to go
                nx, ny = st.x + ahead * math.cos(st.yaw), st.y + ahead * math.sin(st.yaw)
                entering = self.bridge.footprint_blocked(nx, ny, 0.0)
                if (
                    entering and entering != self.bridge.footprint_blocked(st.x, st.y, 0.0)
                ) or self.policy.forbidden_zone(self.zones.zone_of(nx, ny)):
                    break
                self.gate.move_velocity(v, 0.0, 0.0, until_m=abs(r))
                time.sleep(0.02)
            self.gate.stop()

        # 1) to the docking point (skip if already there). A short hop may be walked backwards: pick forward or
        #    backward by the TOTAL rotation, including the final turn to face the target (comparing only the first
        #    turn can choose a hop that needs a 180 deg turn at the end and blows the time budget)
        if travel > 0.03:
            heading = math.atan2(gy - s.y, gx - s.x)
            final = math.atan2(ty - gy, tx - gx)
            back = _wrap(heading + math.pi)
            turn_fwd = abs(_wrap(heading - s.yaw)) + abs(_wrap(final - heading))
            turn_back = abs(_wrap(back - s.yaw)) + abs(_wrap(final - back))
            if travel <= 0.6 and turn_back < turn_fwd:
                heading = back
            turn_to(heading)
            walk_until(lambda st: (gx - st.x) * math.cos(st.yaw) + (gy - st.y) * math.sin(st.yaw))
        # 2) face the target, 3) close to the exact stand-off straight on
        st = self.bridge.state()
        turn_to(math.atan2(ty - st.y, tx - st.x))
        walk_until(lambda st: (tx - st.x) * math.cos(st.yaw) + (ty - st.y) * math.sin(st.yaw) - stand_off)
        st = self.gate.stop_and_settle()
        along = (tx - st.x) * math.cos(st.yaw) + (ty - st.y) * math.sin(st.yaw) - stand_off
        across = -(tx - st.x) * math.sin(st.yaw) + (ty - st.y) * math.cos(st.yaw)
        if abs(along) > 0.03 or abs(across) > 0.03:
            why = "timed out" if timed_out() else "stopped"
            return fail(
                f"could not dock at {target_body} ({why}): {along * 100:+.0f} cm along, "
                f"{across * 100:+.0f} cm across from the docking point"
            )
        return None

    def pick(self, obj: str = "tray") -> SkillResult:
        def run():
            s = self.bridge.state()
            if obj not in self.policy.allowed_objects:
                return SkillResult(
                    False, "pick", f"{obj!r} not in allowed objects {self.policy.allowed_objects}", s.dict()
                )
            if s.holding:
                return SkillResult(False, "pick", f"already holding {s.holding}", s.dict())
            if s.mode == "walking":
                s = self.gate.stop_and_settle()
            if getattr(self.bridge, "mounted", None):  # quadruped: the load rides on its back mount
                return self._mount_pick(obj)
            support = self.bridge.prop_support(obj)  # None on bodies without perception
            if support is not None and support not in SURFACE_POSE:
                return SkillResult(
                    False, "pick", f"cannot reach {obj}: it is on {support}", s.dict(), extra={"out_of_reach": True}
                )
            if getattr(self.bridge, "table_body", False):
                return self._table_pick(obj)
            if getattr(self.bridge, "task_level", False):
                return self._task_gesture("pick", obj)
            fam = SURFACE_POSE.get(support, "SHELF")
            r = self.approach(obj, stand_off=self.P["GRASP_FWD"][fam])
            if r is not None:
                return SkillResult(
                    False,
                    "pick",
                    f"cannot reach {obj}: {r.message}",
                    r.confirmed_state,
                    extra={k: v for k, v in r.extra.items()},
                )
            self.gate.set_arm_targets(self.P["REACH"][fam], REACH_S)  # rest -> reach (hands open)
            self.gate.set_arm_targets(self.P["CLOSE"][fam], CLOSE_S)  # close on the handles
            d = self.bridge.grasp_distance(obj)
            t0 = time.time()
            while d is not None and d > GRASP_TOL_M and time.time() - t0 < 0.3:
                time.sleep(0.05)
                d = self.bridge.grasp_distance(obj)
            if d is not None and d > GRASP_TOL_M:
                self.gate.set_arm_targets(self.P["REST"], REST_S)
                return SkillResult(
                    False,
                    "pick",
                    f"cannot reach {obj}: hands {d * 100:.0f} cm from it",
                    self.bridge.state().dict(),
                    extra={"out_of_reach": True},
                )
            if self._fail_pick_left > 0:  # staged slippery-tray failure
                self._fail_pick_left -= 1
                self.bridge.attach(obj)
                self._carrying = obj
                self.gate.set_arm_targets(self.P["LIFT"][fam], LIFT_UP_S)  # lift ~10 cm straight up ...
                landed = self.bridge.release(obj)  # ... and lose it
                self._carrying = None
                self.gate.set_arm_targets(self.P["REST"], REST_S)
                extra = {"attempt_failed": True, **({"landed_on": landed} if landed else {})}
                where = f"; it fell onto {landed}" if landed else ""
                return SkillResult(
                    False,
                    "pick",
                    f"grasp failed: {obj} slipped (attempt){where}",
                    self.bridge.state().dict(),
                    extra=extra,
                )
            attached = self.bridge.attach(obj)
            if attached is not False:
                self._carrying = obj
            if attached is False:
                self.gate.set_arm_targets(self.P["REST"], REST_S)
                return SkillResult(
                    False,
                    "pick",
                    f"cannot reach {obj}: grasp did not close on it",
                    self.bridge.state().dict(),
                    extra={"out_of_reach": True},
                )
            self.gate.set_arm_targets(self.P["LIFT"][fam], LIFT_UP_S)  # straight up off the surface
            self.gate.set_arm_targets(self.P["HOLD"], LIFT_CARRY_S)  # then in to carry height
            s = self.bridge.state()
            s.holding = obj
            self._set_holding(obj)
            return SkillResult(True, "pick", f"holding {obj}", self.bridge.state().dict())

        return self._guard("pick", run, object=obj)

    def place(self, target: str = "table_B") -> SkillResult:
        def run():
            s = self.bridge.state()
            if target not in self.policy.place_targets:
                return SkillResult(
                    False, "place", f"{target!r} not a known place target {self.policy.place_targets}", s.dict()
                )
            if not s.holding:
                return SkillResult(False, "place", "not holding anything", s.dict())
            if s.mode == "walking":
                self.gate.stop_and_settle()
            obj = s.holding
            if getattr(self.bridge, "mounted", None):
                return self._mount_place(obj, target)
            if getattr(self.bridge, "table_body", False):
                return self._table_place(obj, target)
            if getattr(self.bridge, "task_level", False):
                return self._task_gesture("place", obj, target)
            fam = SURFACE_POSE.get(target, "SHELF")
            r = self.approach(target, obj, stand_off=self.P["GRASP_FWD"][fam])  # keep holding if we cannot get there
            if r is not None:
                return SkillResult(
                    False,
                    "place",
                    f"cannot place {obj} on {target}: {r.message}",
                    r.confirmed_state,
                    extra={k: v for k, v in r.extra.items()},
                )
            self.gate.set_arm_targets(self.P["LIFT"][fam], OVER_S)  # hold -> just above the slot
            self.gate.set_arm_targets(self.P["CLOSE"][fam], DOWN_S)  # straight down, still gripping
            landed = self.bridge.release(obj, target)  # sim: where it physically came to rest
            self._carrying = None
            self.gate.set_arm_targets(self.P["REST"], REST_S)
            self._set_holding(None)
            if landed is not None and landed != target:
                return SkillResult(
                    False,
                    "place",
                    f"released {obj} but it came to rest on {landed}, not {target}",
                    self.bridge.state().dict(),
                    extra={"landed_on": landed},
                )
            extra = {"landed_on": landed} if landed else {}
            return SkillResult(True, "place", f"placed on {target}", self.bridge.state().dict(), extra=extra)

        return self._guard("place", run, target=target)

    # ------------------------------------------------------------- task-level bodies (AgiBot A2)
    # The robot's own planner and recorded gestures do the motion. Our zone and behaviour policy has already run
    # (navigate_to's forbidden-zone / path / predictor checks, pick/place's object and target checks); here we only
    # start the vendor task, watch its status and turn pause / stop into ActionPause / ActionCancel.
    def _task_navigate(self, zone: str, tx: float, ty: float, limit_s: float) -> SkillResult:
        b = self.bridge
        b.start_navigation(zone, tx, ty)
        t0 = time.time()
        st = b.task_status()
        while time.time() - t0 < limit_s:
            self._check_pause()  # Paused: the task stays paused on the robot (ActionPause from Skills.pause)
            st = b.task_status()
            if st.endswith(("SUCCESS", "FAILED")):
                break
            time.sleep(0.1)
        else:
            b.stop()  # ActionCancel
            return SkillResult(
                False, "navigate_to", f"timeout {limit_s:.0f}s: navigation task cancelled", b.state().dict()
            )
        ok = st.endswith("SUCCESS")
        b.finish_navigation(ok)
        s = b.state()
        return SkillResult(
            ok,
            "navigate_to",
            "arrived" if ok else f"navigation task ended {st}",
            s.dict(),
            extra={"final_zone": s.zone, "task_state": st, "pose_source": b.pose_source},
        )

    def _task_gesture(self, skill: str, obj: str, target: str | None = None) -> SkillResult:
        ok = self.bridge.play_gesture(skill)
        if ok:
            self._carrying = obj if skill == "pick" else None
            self.bridge.set_holding(obj if skill == "pick" else None)
            self._set_holding(obj if skill == "pick" else None)
        what = f"{skill} gesture played" + (f" at {target}" if target else "") + " (recorded motion; no grasp sensing)"
        return SkillResult(ok, skill, what if ok else f"{skill} gesture did not finish", self.bridge.state().dict())

    # ------------------------------------------------------------- back-mount bodies (quadrupeds, no arms)
    # The tray (towels in it) is strapped on the back from the start (Go2 behaviour). pick = confirm the load is
    # on board; place = dock beside the slot and hand it off onto the surface. Same zone/behaviour policy and
    # docking bounds as every other body; nothing here moves an arm.
    MOUNT_STAND_OFF_M = 0.65  # base centre -> place slot when docking for a hand-off (config: mount_stand_off)

    def _mount_pick(self, obj):
        b = self.bridge
        if not b.carries(obj):
            return SkillResult(
                False,
                "pick",
                f"cannot pick {obj}: no arms; this body only carries the {b.mounted} on its back",
                b.state().dict(),
                extra={"out_of_reach": True},
            )
        if not b.mount_on():
            return SkillResult(
                False,
                "pick",
                f"cannot pick {obj}: no arms, and the {b.mounted} is no longer on my back (staff must load it)",
                b.state().dict(),
                extra={"out_of_reach": True},
            )
        self._set_holding(obj)
        msg = f"holding {obj}" + (f" (in the {b.mounted} on my back)" if obj != b.mounted else " (on my back)")
        return SkillResult(True, "pick", msg, b.state().dict(), extra={"mounted": b.mounted})

    def _mount_place(self, obj, target):
        b = self.bridge
        if not b.mount_on():
            self._set_holding(None)
            return SkillResult(False, "place", f"the {b.mounted} is no longer on my back", b.state().dict())
        stand = float((b.cfg.get("body") or {}).get("mount_stand_off", self.MOUNT_STAND_OFF_M))
        r = self.approach(target, obj, stand_off=stand)  # bounded docking, zone + footprint checks
        if r is not None:
            return SkillResult(
                False,
                "place",
                f"cannot place {obj} on {target}: {r.message}",
                r.confirmed_state,
                extra={k: v for k, v in r.extra.items()},
            )
        res = b.unmount(obj, target)
        if not res.get("ok"):
            return SkillResult(False, "place", f"cannot place {obj} on {target}: {res.get('error')}", b.state().dict())
        self._set_holding(None)
        landed = res.get("landed")
        if landed is not None and landed != target:
            return SkillResult(
                False,
                "place",
                f"handed {obj} off but it came to rest on {landed}, not {target}",
                b.state().dict(),
                extra={"landed_on": landed},
            )
        return SkillResult(
            True,
            "place",
            f"placed on {target} (handed off from my back: no arms; in the venue staff lift it off)",
            b.state().dict(),
            extra={"landed_on": landed, "handoff": True},
        )

    # ------------------------------------------------------------- table bodies (fixed-base arms)
    # navigate_to(zone) becomes move_to_region(zone): the same zone policy (checked above), the regions are the
    # venue's zones drawn on the arm's work table. pick/place work on the region under the hand.
    TABLE_TARGET_REGION = {"table_B": "B", "shelf_corridor": "corridor", "table_A": "A"}

    def _table_move(self, zone: str) -> SkillResult:
        self.bridge.move_to_region(zone)
        s = self.bridge.state()
        return SkillResult(True, "navigate_to", "arrived", s.dict(), extra={"region": zone, "table_body": True})

    def _table_pick(self, obj: str) -> SkillResult:
        s = self.bridge.state()
        where = self.bridge.region_of(obj)
        if where != s.zone:
            return SkillResult(
                False,
                "pick",
                f"cannot reach {obj}: it is in region {where}, the hand is over {s.zone}; navigate_to({where}) first",
                s.dict(),
                extra={"out_of_reach": True},
            )
        low, hover = self.P["LOW"][where], self.P["HOVER"][where]
        self.gate.set_arm_targets(low, REACH_S)
        d = self.bridge.grasp_distance(obj)
        if d is not None and d > GRASP_TOL_M:
            self.gate.set_arm_targets(hover, REST_S)
            return SkillResult(
                False,
                "pick",
                f"cannot reach {obj}: hand {d * 100:.0f} cm from it",
                self.bridge.state().dict(),
                extra={"out_of_reach": True},
            )
        if self._fail_pick_left > 0:
            self._fail_pick_left -= 1
            self.bridge.attach(obj)
            self.gate.set_arm_targets(hover, LIFT_UP_S)
            landed = self.bridge.release(obj)
            self._carrying = None
            extra = {"attempt_failed": True, **({"landed_on": landed} if landed else {})}
            return SkillResult(
                False,
                "pick",
                f"grasp failed: {obj} slipped (attempt)" + (f"; it fell onto {landed}" if landed else ""),
                self.bridge.state().dict(),
                extra=extra,
            )
        if self.bridge.attach(obj) is False:
            self.gate.set_arm_targets(hover, REST_S)
            return SkillResult(
                False,
                "pick",
                f"cannot reach {obj}: grasp did not close on it",
                self.bridge.state().dict(),
                extra={"out_of_reach": True},
            )
        self.gate.set_arm_targets(hover, LIFT_CARRY_S)
        self._set_holding(obj)
        return SkillResult(True, "pick", f"holding {obj}", self.bridge.state().dict())

    def _table_place(self, obj: str, target: str) -> SkillResult:
        s = self.bridge.state()
        region = self.TABLE_TARGET_REGION.get(target, target)
        if region != s.zone:
            return SkillResult(
                False,
                "place",
                f"cannot place {obj} on {target}: the hand is over region {s.zone}; navigate_to({region}) first",
                s.dict(),
                extra={"out_of_reach": True},
            )
        self.gate.set_arm_targets(self.P["LOW"][region], DOWN_S + OVER_S)
        self.bridge.release(obj, target)
        self._carrying = None
        landed = self.bridge.region_of(obj)
        self.gate.set_arm_targets(self.P["HOVER"][region], REST_S)
        self._set_holding(None)
        if landed != region:
            return SkillResult(
                False,
                "place",
                f"released {obj} but it came to rest in region {landed}, not {target}",
                self.bridge.state().dict(),
                extra={"landed_on": f"region {landed}"},
            )
        return SkillResult(
            True, "place", f"placed on {target}", self.bridge.state().dict(), extra={"landed_on": f"region {region}"}
        )

    def say(self, text: str) -> SkillResult:
        def run():
            words = text.split()
            if len(words) > 20:
                text2 = " ".join(words[:20])
            else:
                text2 = text
            self.bridge.say(text2)
            return SkillResult(True, "say", text2, self.bridge.state().dict())

        return self._guard("say", run, text=text[:60])

    def look_at(self, target: str) -> SkillResult:
        def run():
            self.bridge.look_at(target)
            time.sleep(0.5)
            return SkillResult(True, "look_at", f"looking at {target}", self.bridge.state().dict())

        return self._guard("look_at", run, target=target)

    def hold_pose(self, reason: str) -> SkillResult:
        def run():
            s = self._hold(reason)
            return SkillResult(
                True, "hold_pose", f"holding pose: {reason}", s.dict(), extra={"handover": "handover" in reason.lower()}
            )

        return self._guard("hold_pose", run, reason=reason[:80])

    def run_recover(self, go, speed: float = 0.15, max_m: float = 1.5, max_s: float = 8.0, margin: float = 0.3) -> dict:
        """Recovery into the go zone (safety/recover.py) as a traced turn. Called by the safety supervisor on Reset."""
        from safety.recover import recover_to_go_zone

        out: dict = {}

        def run():
            out.update(recover_to_go_zone(self, go, speed=speed, margin=margin, max_m=max_m, max_s=max_s))
            return SkillResult(
                out["ok"],
                "recover",
                out["detail"],
                self.bridge.state().dict(),
                extra={"moved_m": round(out["moved_m"], 2)},
            )

        r = self._guard("recover", run)
        if not out:
            out = {"ok": False, "detail": r.message, "moved_m": 0.0}
        return out

    # ------------------------------------------------------------------ guide (venue concierge)
    def venue(self):
        v = getattr(self, "_venue", None)
        if v is None:
            from venue.facts import Venue

            v = self._venue = Venue(ROOT / self.cfg.get("venue_file", "venue/leukerbad_therme.yaml"))
        return v

    def _leg_clear(self, x0, y0, x1, y1, clearance: float = 0.5) -> bool:
        """The straight leg and two copies offset sideways by `clearance` stay out of forbidden zones (navigate_to
        looks 0.5 m ahead while steering, so a line grazing a no-go corner would be refused mid-way)."""
        d = math.hypot(x1 - x0, y1 - y0) or 1e-9
        nx, ny = -(y1 - y0) / d * clearance, (x1 - x0) / d * clearance
        return all(not self._path_forbidden(x0 + k * nx, y0 + k * ny, x1 + k * nx, y1 + k * ny) for k in (0, -1, 1))

    def _route(self, zone: str) -> list[str] | None:
        """Zones to drive through: direct if the leg is clear with margin, else via one hub zone (venue `hubs`)."""
        s = self.bridge.state()
        tx, ty = self.zones.centre(zone)
        if self._leg_clear(s.x, s.y, tx, ty):
            return [zone]
        for hub in self.venue().raw.get("hubs") or []:
            if hub == zone or hub not in self.zones.names():
                continue
            hx, hy = self.zones.centre(hub)
            if self._leg_clear(s.x, s.y, hx, hy) and self._leg_clear(hx, hy, tx, ty):
                return [hub, zone]
        return None

    def guide_to(self, poi: str, lang: str = "en") -> SkillResult:
        """Guide a guest to a point of interest: say(intro) -> navigate_to each leg (zone policy, speed caps, safety
        FSM: CAUTION slows, HOLD stops, control-plane loss holds at the waypoint) -> say(arrival). No-go POIs are
        refused before anything moves, with the nearest allowed alternative; a POI with requires_approval also asks
        ClawMetry (Approve/Deny) whether STAFF should escort the guest. Each say / leg is its own traced turn."""
        t0 = time.time()
        v = self.venue()
        p = v.poi(poi)
        if p is None:
            return SkillResult(
                False,
                "guide_to",
                f"unknown place {poi!r}; known: {[x['id'] for x in v.list_pois()]}",
                self.bridge.state().dict(),
            )
        pid = p["id"]
        name = v.name(pid, lang)
        self.emit("guide", phase="request", poi=pid, lang=lang)
        if p.get("no_go"):
            near = p.get("nearest")
            why = v.text(pid, "no_go", lang) or "it is a no-go area"
            extra = {
                "blocked_by_policy": True,
                "poi": pid,
                "nearest": near,
                "nearest_name": v.name(near, lang) if near else None,
            }
            if p.get("requires_approval"):
                extra["staff_escort"] = self._ask_staff_escort(pid, lang)
            self.emit("guide", phase="refused", poi=pid, nearest=near, reason=why[:160])
            return SkillResult(
                False,
                "guide_to",
                f"blocked_by_policy: I can't go to the {name}: {why}",
                self.bridge.state().dict(),
                extra=extra,
            )
        held = self.safety.check_skill("guide_to") if self.safety is not None else None
        if held:
            return SkillResult(
                False, "guide_to", f"held by safety: {held}", self.bridge.state().dict(), extra={"poi": pid}
            )
        if self._pause.is_set():
            return SkillResult(
                False, "guide_to", "robot is paused by operator; wait for resume", self.bridge.state().dict()
            )
        route = self._route(p["zone"])
        if route is None:
            return SkillResult(
                False,
                "guide_to",
                f"blocked_by_policy: no allowed route to the {name}",
                self.bridge.state().dict(),
                extra={"blocked_by_policy": True, "poi": pid},
            )
        per = getattr(self, "perception", None)
        if per is not None:
            per.intent = {"poi": pid, "action": f"forward 2 m at 0.3 m/s toward {pid}"}
        if self.safety is not None:
            self.safety.request_judge("guide_start")
        self.say(v.text(pid, "intro", lang) or f"Follow me to the {name}.")
        limit = self.policy.skill_limit("guide_to", "max_duration_s") or 240.0
        legs = []
        try:
            for i, z in enumerate(route):
                if self.safety is not None and i > 0:
                    self.safety.at_waypoint(route[i - 1])
                left = limit - (time.time() - t0)
                if left <= 1:
                    break
                r = self.navigate_to(z, max_s=min(left, self.nav_max_s))
                legs.append({"zone": z, "ok": r.ok, "message": r.message[:160]})
                if not r.ok:
                    self.emit("guide", phase="stopped", poi=pid, leg=z, reason=r.message[:160])
                    return SkillResult(
                        False,
                        "guide_to",
                        f"stopped on the way to the {name}: {r.message}",
                        r.confirmed_state,
                        extra={"poi": pid, "legs": legs, "route": route},
                    )
        except SafetyHold as e:
            s = self.gate.stop_and_settle()
            return SkillResult(
                False, "guide_to", f"held by safety: {e}", s.dict(), extra={"poi": pid, "legs": legs, "route": route}
            )
        finally:
            if per is not None:
                per.intent = {}
        self.say(v.text(pid, "arrival", lang) or f"Here is the {name}.")
        s = self.bridge.state()
        self.emit("guide", phase="arrived", poi=pid, zone=s.zone, duration_s=round(time.time() - t0, 1))
        return SkillResult(
            True,
            "guide_to",
            f"arrived at the {name}",
            s.dict(),
            duration_ms=(time.time() - t0) * 1000,
            extra={"poi": pid, "legs": legs, "route": route},
        )

    def _ask_staff_escort(self, pid: str, lang: str) -> str:
        """Background ClawMetry approval: may staff escort the guest to a no-go POI? The robot never goes either way."""
        appr = getattr(self.safety, "approvals", None) if self.safety is not None else None
        v = self.venue()

        def work():
            pend = self.escort_pending = {
                "poi": pid,
                "lang": lang,
                "event": threading.Event(),
                "decision": None,
                "by": None,
            }
            if appr is None:  # no ClawMetry approvals configured on this edge: the console decides
                r = {"decision": "no_rule"}
            else:
                r = appr.request(
                    "mcp__g1-skills__guide_to",
                    {"poi": pid, "robot": self.bridge.body, "ask": "staff escort for a guest"},
                )
            if r["decision"] in ("no_rule", "unavailable"):
                # no ClawMetry Guard rule (or ClawMetry unreachable): the console's Approve / Deny decides (edge key)
                self.emit("approval", tool="guide_to", poi=pid, decision="pending_console", clawmetry=r["decision"])
                pend["event"].wait(120.0)
                r = {
                    "decision": pend["decision"] or "unavailable",
                    "approval_id": None,
                    "waited_s": None,
                    "by": pend["by"],
                }
            self.escort_pending = None
            self.emit(
                "approval",
                tool="guide_to",
                poi=pid,
                decision=r["decision"],
                approval_id=r.get("approval_id"),
                waited_s=r.get("waited_s"),
                by=r.get("by") or "ClawMetry",
            )
            near = (v.pois.get(pid) or {}).get("nearest")
            if r["decision"] == "allow":
                self.emit("alert_staff", reason=f"escort a guest to {pid} (approved)", poi=pid)
                self.say(
                    {
                        "de": "Ein Mitarbeiter kommt gleich und begleitet Sie.",
                        "fr": "Un membre du personnel arrive pour vous accompagner.",
                        "it": "Un membro dello staff arriva subito per accompagnarla.",
                    }.get(lang, "A staff member is on the way to accompany you.")
                )
            elif r["decision"] == "deny":
                self.say(
                    {
                        "de": f"Das Personal kann Sie gerade nicht begleiten. Ich bringe Sie gern zum {v.name(near, 'de')}.",
                        "fr": f"Le personnel ne peut pas vous accompagner pour l'instant. Je peux vous conduire à {v.name(near, 'fr')}.",
                        "it": f"Lo staff non può accompagnarla ora. Posso portarla a {v.name(near, 'it')}.",
                    }.get(
                        lang, f"Staff can't escort you right now. I can take you to the {v.name(near, 'en')} instead."
                    )
                )

        threading.Thread(target=work, daemon=True, name="staff-escort").start()
        return "requested"

    # ------------------------------------------------------------------ util
    def _path_forbidden(self, x0, y0, x1, y1, step=0.1) -> str | None:
        """First zone along a straight path the behaviour policy forbids entering, else None."""
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
        for i in range(n + 1):
            a = i / n
            z = self.zones.zone_of(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a)
            if self.policy.forbidden_zone(z):
                return z
        return None

    def _set_holding(self, obj):
        # bridges keep their own state object; set via a tiny protocol
        st = getattr(self.bridge, "_s", None)
        lock = getattr(self.bridge, "_lock", None)
        if st is not None and lock is not None:
            with lock:
                st.holding = obj


_SINGLETON: Skills | None = None


def get_skills() -> Skills:
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = Skills()
    return _SINGLETON
