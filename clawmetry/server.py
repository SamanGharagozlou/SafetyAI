#!/usr/bin/env python3
"""ClawMetry physical-AI collector: one process, zero dependencies.

    python3 server.py --upstream http://127.0.0.1:5175 --body agibot_d1w

It is four things at once, because at a venue four processes is three too many:

  THE TAP        POST /vapi is a drop-in replacement for the robot's tool
                 endpoint. Point Vapi (or the console) here instead of at the
                 robot: every call is recorded, predicted, gated and then
                 forwarded upstream unchanged. This is the only place in the
                 whole system that sees WHO ASKED and WHAT WAS INTENDED, and
                 it is on hardware you own - unlike the transcript, which is
                 Vapi's self-report.
  THE COLLECTORS background pollers for the bridge (confirmed physical state)
                 and the camera guard (people, breaches), plus POST /ingest
                 for anything else - ROS, approvals, a second robot.
  THE LEDGER     hash-chained, append-only, fsynced (ledger.py).
  THE DASHBOARD  GET / - one page, live over SSE.

THE TAP FORWARDS EVEN WHEN IT CANNOT GATE. An observer that breaks the robot
when it fails is an observer nobody installs twice. The one exception is an
explicit deny, which is the whole point of the gate.

Read-only with respect to the robot, by construction: the tap never originates
a command, it only passes through or refuses. Compromising this process cannot
make the robot do anything a caller did not already ask for. That is deliberate
- fleet-wide oversight that can also actuate is a fleet-wide remote control.
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from correlate import Correlator, Policy
from ledger import Ledger
from frame import FrameGrabber
from predict import BrainJudge, CosmosJudge, kinematic_predict, verify

HERE = Path(__file__).resolve().parent
UNGATED = {"stop_robot", "robot_status", "get_state", "pause"}


class Hub:
    """Ledger + correlator + collectors + subscribers."""

    def __init__(self, args):
        self.args = args
        self.led = Ledger(Path(args.ledger), robot_id=args.robot_id)
        self.cor = Correlator(Policy(speed_cap_mps=args.speed_cap))
        # Prefer eaby's brain relay over vLLM direct: it carries his prompt and
        # verdict schema and files the verdict in his system too, so the two
        # dashboards cannot disagree about the same robot.
        self.judge = (BrainJudge(args.brain_url, args.brain_key)
                      if args.brain_url and args.brain_key else CosmosJudge())
        self.frames = FrameGrabber(args.rtsp)
        if args.judge:
            self.frames.start()          # keep a warm frame; judge latency only
        self.zones = self._load_zones(args.zones)
        self._call_id = ""
        self.subs: list[queue.Queue] = []
        self.last_state: dict = {}
        self.breaches: list[dict] = []
        self.sources: dict = {}
        self._n = 0
        self.cor.expect("bridge")
        if args.guard:
            self.cor.expect("guard")
        self.led.append("integrity", actor="system", data={
            "event": "collector_started", "body": args.body,
            "upstream": args.upstream, "guard": args.guard or None,
            "cosmos": self.judge.configured})

    @staticmethod
    def _load_zones(path):
        if not path or not Path(path).exists():
            return []
        try:
            return json.loads(Path(path).read_text())
        except Exception:
            return []

    # -- the one place events enter -------------------------------------

    def emit(self, kind, actor="system", data=None, corr="", src_ts=None) -> dict:
        ev = self.led.append(kind, actor=actor, data=data or {}, corr=corr, src_ts=src_ts)
        if kind == "state":
            self.last_state = ev["data"]
        src = (data or {}).get("source") or actor
        self.sources[src] = {"last_ts": ev["ts"], "kind": kind}
        for b in self.cor.feed(ev):
            self._record_breach(b)
        self._fanout(ev)
        return ev

    def _record_breach(self, b: dict):
        ev = self.led.append("breach", actor="system", data=b, corr=b.get("corr", ""))
        self.breaches.insert(0, ev["data"] | {"seq": ev["seq"], "ts": ev["ts"]})
        del self.breaches[200:]
        self._fanout(ev)

    def _fanout(self, ev):
        for q in list(self.subs):
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=500)
        self.subs.append(q)
        return q

    def unsubscribe(self, q):
        if q in self.subs:
            self.subs.remove(q)

    def corr_id(self) -> str:
        self._n += 1
        return f"act_{int(time.time())}_{self._n}"

    # -- the tap ---------------------------------------------------------

    def handle_tool(self, name: str, args_: dict, raw: dict, call_id: str = "") -> dict:
        """Record -> predict -> gate -> forward -> score. Returns the upstream reply."""
        corr = self.corr_id()
        self.emit("intent", actor="agent", corr=corr,
                  data={"tool": name, "args": args_, "source": "vapi_tap"})

        decision, reason = "allow", ""
        pred = None
        if name not in UNGATED:
            pred = kinematic_predict(name, args_, self.last_state, self.args.body, self.zones)
            self.emit("prediction", actor="system", corr=corr, data=pred.as_dict())
            if pred.predicted_breach:
                decision, reason = "deny", pred.reason
            # Cosmos judges behind the kinematic gate, never in front of it.
            # Only translating actions get a world-model opinion. The
            # kinematic predictor already knows the distance; a gesture is
            # 0 m and the judge's "is the path ahead clear" question simply
            # does not apply to it. Asking anyway denied a nod in a room with
            # eight people in it, and cost 8 s doing so.
            translates = pred is not None and pred.distance_m > 0.01
            if self.judge.configured and self.args.judge and not translates:
                self.emit("prediction", actor="system", corr=corr,
                          data={"basis": "cosmos", "available": False,
                                "skipped": True, "predicted_breach": False,
                                "reason": f"{name} does not translate the robot "
                                          f"(0 m): no world-model question to ask"})
            if self.judge.configured and self.args.judge and translates:
                jpeg = (self.frames.grab(self.last_state.get("zone", ""))
                        if isinstance(self.judge, BrainJudge) else b"")
                v = (self.judge.judge(name, args_, self._scene(), jpeg)
                     if isinstance(self.judge, BrainJudge)
                     else self.judge.judge(name, args_, self._scene()))
                if not jpeg and isinstance(self.judge, BrainJudge):
                    v = {"available": False, "reason": self.frames.last_error or "no frame"}
                self.emit("prediction", actor="system", corr=corr,
                          data={"basis": "cosmos", **v,
                                "predicted_breach": bool(v.get("unsafe"))})
                if v.get("available") and v.get("unsafe"):
                    decision, reason = "deny", v.get("reason", "world model refused")

        self.emit("decision", actor="gate", corr=corr,
                  data={"decision": decision, "reason": reason, "tool": name})
        if decision == "deny":
            return {"results": [{"toolCallId": call_id,
                                 "result": f"Refused: {reason}"}]}

        t0 = time.time()
        self._call_id = call_id
        before = dict(self.last_state)
        self.emit("command", actor="agent", corr=corr, data={"tool": name, "args": args_})
        reply, err = self._forward(raw)
        dt = time.time() - t0

        after = self._poll_bridge_once() or dict(self.last_state)
        pose = ([after.get("x"), after.get("y"), after.get("yaw", 0.0)]
                if after.get("has_pose") else None)
        # Score against the prediction we actually made. An earlier version
        # scored against the START pose with a 0 s duration, which turned every
        # successful action into a large fake deviation.
        scored = (verify(pred.as_dict(), {"end_pose": pose, "duration_s": dt}, self.args.body)
                  if pred else {"deviation_z": 0.0, "basis": "none", "duration_scored": False})
        self.emit("outcome", actor="robot", corr=corr,
                  data={"tool": name, "duration_s": round(dt, 2), "end_pose": pose,
                        "error": err, **scored})
        return reply

    def _scene(self) -> dict:
        s = self.last_state
        return {k: s.get(k) for k in
                ("nearest_person_m", "people_within_2m", "child_near", "zone", "moving")}

    def _forward(self, raw: dict):
        body = json.dumps(raw).encode()
        headers = {"content-type": "application/json"}
        if self.args.token:
            headers["authorization"] = f"Bearer {self.args.token}"
        try:
            req = urllib.request.Request(f"{self.args.upstream}/vapi", data=body,
                                         headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.args.timeout) as r:
                return json.loads(r.read() or b"{}"), None
        except Exception as e:
            # Forward failures are recorded, never swallowed, and never
            # reported upstream as success.
            return ({"results": [{"toolCallId": self._call_id,
                                  "result": f"Robot endpoint unreachable: {e}"}]}, str(e))

    # -- collectors -------------------------------------------------------

    def _poll_bridge_once(self) -> dict | None:
        tok = self.args.state_token
        headers = {"authorization": f"Bearer {tok}"} if tok else {}
        try:
            req = urllib.request.Request(f"{self.args.state_url}/status", headers=headers)
            with urllib.request.urlopen(req, timeout=3.0) as r:
                s = json.loads(r.read())
            self._poll_err = 0
        except Exception as e:
            # A collector that cannot collect is a finding, not a blank. Report
            # the first failure and then every 30th, so a dead feed is loud once
            # and does not drown the ledger.
            self._poll_err = getattr(self, "_poll_err", 0) + 1
            if self._poll_err == 1 or self._poll_err % 30 == 0:
                self.emit("integrity", actor="system",
                          data={"event": "collector_failing", "collector": "bridge",
                                "target": f"{self.args.state_url}/status",
                                "consecutive": self._poll_err, "error": str(e)})
            return None
        has_pose = "x" in s and "y" in s
        d = {"source": "bridge", "connected": bool(s.get("connected")),
             "moving": bool(s.get("moving")), "ctrl_mode": s.get("ctrl_mode"),
             "battery_pct": s.get("battery_pct"), "gait": s.get("gait"),
             "has_pose": has_pose, "speed_mps": s.get("speed_mps")}
        if has_pose:
            d |= {"x": s.get("x"), "y": s.get("y"), "yaw": s.get("yaw", 0.0)}
        self.emit("state", actor="robot", data=d, src_ts=s.get("ts"))
        return d

    def run_collectors(self):
        threading.Thread(target=self._loop_bridge, daemon=True).start()
        if self.args.guard:
            threading.Thread(target=self._loop_guard, daemon=True).start()
        threading.Thread(target=self._loop_tick, daemon=True).start()

    def _loop_bridge(self):
        while True:
            self._poll_bridge_once()
            time.sleep(self.args.state_hz and 1.0 / self.args.state_hz or 1.0)

    def _loop_guard(self):
        while True:
            try:
                with urllib.request.urlopen(f"{self.args.guard}/guard", timeout=2.0) as r:
                    g = json.loads(r.read())
                self.emit("state", actor="robot", data={
                    "source": "guard", "state": g.get("state"), "zone": g.get("zone"),
                    "nearest_person_m": g.get("nearest_m"), "people": g.get("people"),
                    "child_near": g.get("child_near"), "latched": g.get("latched"),
                    "stale": g.get("stale"), "moving": False})
            except Exception:
                pass
            time.sleep(1.0)

    def _loop_tick(self):
        while True:
            for b in self.cor.tick(time.time()):
                self._record_breach(b)
            time.sleep(0.5)

    # -- snapshot ---------------------------------------------------------

    def snapshot(self) -> dict:
        now = time.time()
        return {
            "ts": round(now, 3),
            "run_id": self.led.run_id, "robot_id": self.led.robot_id,
            "body": self.args.body,
            "chain": self.led.verify(),
            "sources": {k: {**v, "age_s": round(now - v["last_ts"], 1)}
                        for k, v in self.sources.items()},
            "robot": self.last_state,
            "open_actions": [{"corr": o.corr, "tool": o.tool, "decision": o.decision,
                              "moved": o.moved, "age_s": round(now - o.t_intent, 1)}
                             for o in self.cor.open.values()],
            "breaches": self.breaches[:40],
            "counts": self._counts(),
            "cosmos": {"configured": self.judge.configured, "enabled": bool(self.args.judge)},
            "upstream": self.args.upstream,
        }

    def _counts(self) -> dict:
        c: dict = {}
        for ev in self.led.read():
            c[ev["kind"]] = c.get(ev["kind"], 0) + 1
        return c


class Handler(BaseHTTPRequestHandler):
    hub: Hub = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode())

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            return self._send(200, (HERE / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
        if p == "/api/state":
            return self._json(200, self.hub.snapshot())
        if p == "/api/ledger":
            return self._json(200, {"events": self.hub.led.read(limit=300)})
        if p == "/api/verify":
            return self._json(200, self.hub.led.verify())
        if p == "/api/events":
            return self._sse()
        return self._json(404, {"error": "not found"})

    def _sse(self):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-store")
        self.send_header("connection", "keep-alive")
        self.end_headers()
        q = self.hub.subscribe()
        try:
            for ev in self.hub.led.read(limit=30):
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=1.0)
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")   # silence must look alive
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.hub.unsubscribe(q)

    def do_POST(self):
        p = self.path.split("?")[0]
        n = int(self.headers.get("content-length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})

        if p == "/vapi":
            calls = (((payload.get("message") or {}).get("toolCallList")) or [])
            results = []
            for c in calls:
                a = c.get("arguments") or c.get("function", {}).get("arguments") or {}
                if isinstance(a, str):
                    try:
                        a = json.loads(a)
                    except ValueError:
                        a = {}
                name = c.get("name") or c.get("function", {}).get("name") or ""
                out = self.hub.handle_tool(name, a, payload, c.get("id", ""))
                results.extend(out.get("results") or [])
            return self._json(200, {"results": results})

        if p == "/ingest":
            ev = self.hub.emit(payload.get("kind", "integrity"),
                               actor=payload.get("actor", "system"),
                               data=payload.get("data") or {},
                               corr=payload.get("corr", ""),
                               src_ts=payload.get("src_ts"))
            return self._json(200, {"ok": True, "seq": ev["seq"]})

        if p == "/intervention":
            # WHO INTERVENED: a human tapped Approve / Deny / Pause.
            self.hub.emit("intervention", actor="staff", corr=payload.get("corr", ""),
                          data={"decision": payload.get("decision"),
                                "by": payload.get("by", "operator"),
                                "reason": payload.get("reason", "")})
            return self._json(200, {"ok": True})

        return self._json(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--upstream", default="http://127.0.0.1:5175",
                    help="the robot tool endpoint this tap forwards to")
    ap.add_argument("--state-url", default="",
                    help="where to poll confirmed state; defaults to --upstream. "
                         "On the Jetson this is the ros-agent (:8091), which needs no "
                         "token - the edge's GET /status 401s even WITH the right one, "
                         "because its auth check runs before routing.")
    ap.add_argument("--state-token", default="", help="bearer for --state-url, if it needs one")
    ap.add_argument("--guard", default="", help="tools/robot-guard base url")
    ap.add_argument("--body", default="agibot_d1w", choices=["agibot_d1w", "unitree_g1", "fake"])
    ap.add_argument("--robot-id", default="leukerbad-d1w")
    ap.add_argument("--ledger", default="./clawmetry-ledger.jsonl")
    ap.add_argument("--zones", default="", help="JSON list of zone boxes")
    ap.add_argument("--token", default="", help="bearer for the upstream robot")
    ap.add_argument("--speed-cap", type=float, default=0.4)
    ap.add_argument("--state-hz", type=float, default=2.0)
    ap.add_argument("--timeout", type=float, default=40.0)
    ap.add_argument("--brain-url", default="", help="eaby's brain relay, e.g. http://spark:8780")
    ap.add_argument("--brain-key", default="", help="BRAIN_API_KEY (prefer --env-file)")
    ap.add_argument("--rtsp", default="", help="robot camera, for the world-model judge")
    ap.add_argument("--env-file", default="", help="read BRAIN_URL/BRAIN_API_KEY/ROBOT_RTSP "
                                                   "from a .env so keys stay off the command "
                                                   "line and out of `ps`")
    ap.add_argument("--judge", action="store_true", help="enable the Cosmos world-model judge")
    a = ap.parse_args()
    if a.env_file:
        # Keys via file, never argv: anything on the command line is visible to
        # every user on the box through `ps`.
        for line in open(a.env_file):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k == "BRAIN_URL" and not a.brain_url:
                    a.brain_url = v
                elif k == "BRAIN_API_KEY" and not a.brain_key:
                    a.brain_key = v
                elif k == "ROBOT_RTSP" and not a.rtsp:
                    a.rtsp = v
    if not a.state_url:
        a.state_url = a.upstream
        a.state_token = a.token

    try:
        srv = ThreadingHTTPServer((a.host, a.port), Handler)
    except OSError as e:
        raise SystemExit(f"cannot bind {a.host}:{a.port} ({e}). Another collector is "
                         f"probably still running - it would keep serving while this "
                         f"one wrote a ledger nobody reads.")
    hub = Hub(a)
    hub.run_collectors()
    Handler.hub = hub
    print(f"clawmetry collector  http://{a.host}:{a.port}")
    print(f"  body={a.body}  tools->{a.upstream}  state->{a.state_url}  guard={a.guard or 'none'}")
    print(f"  cosmos={'on' if (a.judge and hub.judge.configured) else 'off'}  ledger={a.ledger}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        hub.led.append("integrity", data={"event": "collector_stopped"})
        print("\nstopped; chain:", hub.led.verify())


if __name__ == "__main__":
    main()
