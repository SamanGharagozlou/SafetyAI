#!/usr/bin/env python3
"""
d1-escort - runs a "follow me" escort, checking the guest is still there.

Sits beside d1-guard and does the moving. The guard stays a passive observer
with no idea which way the body points; this process issued the turns, so it is
the one that can keep a visibility ring.

    python3 escort.py --guard http://127.0.0.1:8092 \
                      --robot http://127.0.0.1:8090 --port 8093

    curl -XPOST localhost:8093/start -d '{"distance_cm":450,"destination":"the restaurant"}'
    curl localhost:8093/
    curl -XPOST localhost:8093/stop

TWO HONEST LIMITATIONS, both of which want the same one-line fix in the agent:

  TURNS ARE OPEN-LOOP. `move_robot` takes a direction, a duration and a speed
  FRACTION - not an angle. So a turn is dead-reckoned: degrees = rate x
  seconds, with --turn-deg-per-sec measured once on the robot. Every turn
  carries error, and the ring's bearings drift with it over an escort. The SDK
  has getRPY(), but the bridge's /status does not forward it; forwarding yaw
  would make every turn closed-loop and delete this whole paragraph.

  IT CANNOT SPEAK. Speech belongs to the Vapi layer, which lives on the Jetson
  outside this repo. `say` actions are therefore surfaced as `pending_say` in
  this service's status and logged, for the voice layer to pick up. Until that
  is wired, an escort that loses its guest stops and waits silently, which is
  safe but rude.
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from awareness import FrameQuality, VisibilityRing, wrap180     # noqa: E402
from escort_core import Candidate, Escort, EscortPolicy, Phase  # noqa: E402


class Peer:
    def __init__(self, base, token=""):
        self.base = base.rstrip("/")
        self.token = token
        self.last_error = ""

    def get(self, path, timeout=2.0):
        try:
            req = urllib.request.Request(self.base + path)
            if self.token:
                req.add_header("Authorization", "Bearer %s" % self.token)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            self.last_error = str(e)
            return None

    def tool(self, name, args, timeout=40.0):
        body = json.dumps({"message": {"type": "tool-calls", "toolCallList": [
            {"id": "escort", "name": name, "arguments": args}]}}).encode()
        req = urllib.request.Request(
            self.base + "/vapi", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % self.token})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            return (d.get("results") or [{}])[0].get("result", "")
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            self.last_error = str(e)
            return None


class Runner:
    def __init__(self, guard: Peer, robot: Peer, args):
        self.guard, self.robot, self.args = guard, robot, args
        self.ring = VisibilityRing(stale_after_s=args.stale_after)
        self.escort = Escort(self.ring, EscortPolicy(
            leg_cm=args.leg_cm, look_back_deg=args.look_back,
            follow_max_m=args.follow_max, lagging_m=args.lagging,
            wait_s=args.wait_s, max_missed_checks=args.max_missed))
        self.lock = threading.Lock()
        self.pending_say: list[str] = []
        self.log: list[str] = []
        self.last_guard: dict = {}
        self.busy = ""
        self.dry_run = args.dry_run

    # ------------------------------------------------------------------
    def note(self, line):
        stamp = time.strftime("%H:%M:%S")
        self.log.append("%s %s" % (stamp, line))
        del self.log[:-60]
        print("[escort] %s" % line, flush=True)

    def observe(self, now):
        """Pull the guard's view and fold it into the ring."""
        g = self.guard.get("/guard?appearance=1")
        if g is None:
            self.last_guard = {"state": "UNREACHABLE",
                               "reason": self.guard.last_error,
                               "walk_ok": False}
            return [], False
        self.last_guard = g
        fr = g.get("frame")
        if fr is None:
            # No frame measured yet: say so rather than inventing a usable one.
            q = FrameQuality(mean_luma=0.0)
        else:
            q = FrameQuality(mean_luma=fr["mean_luma"],
                             rms_contrast=fr["rms_contrast"],
                             blur_var=fr["blur_var"],
                             occluded_frac=fr["occluded_frac"])
        people = [_P(p) for p in g.get("people_seen") or []]
        self.ring.observe(self.escort.facing_deg, q, people, now)
        return people, bool(g.get("walk_ok"))

    # ------------------------------------------------------------------
    def do_turn(self, to_bearing, now):
        delta = wrap180(to_bearing - self.escort.facing_deg)
        if abs(delta) < 3.0:
            self.escort.turned(to_bearing)
            return
        secs = min(5.0, abs(delta) / max(self.args.turn_deg_per_sec, 1.0))
        self.busy = "turning %+.0f deg" % delta
        self.note("turn %+.0f deg (%.1fs at %.0f deg/s, open-loop)"
                  % (delta, secs, self.args.turn_deg_per_sec))
        if not self.dry_run:
            r = self.robot.tool("move_robot", {
                "direction": "turn_left" if delta > 0 else "turn_right",
                "duration": round(secs, 2),
                "speed": self.args.turn_speed}, timeout=secs + 15)
            if r is None:
                self.note("turn FAILED: %s" % self.robot.last_error)
                self.escort.abort("could not turn: %s" % self.robot.last_error)
                return
        else:
            time.sleep(min(secs, 0.3))
        # settle, then let the camera catch up before anything is believed
        time.sleep(self.args.settle_s)
        self.escort.turned(to_bearing)
        self.busy = ""

    def do_walk(self, cm, now):
        self.busy = "walking %.0f cm" % cm
        self.note("walk %.0f cm" % cm)
        if not self.dry_run:
            r = self.robot.tool("walk_distance",
                                {"distance_cm": round(min(cm, 200.0)),
                                 "direction": "forward"}, timeout=45)
            if r is None:
                self.note("walk FAILED: %s" % self.robot.last_error)
                self.escort.abort("could not walk: %s" % self.robot.last_error)
                return
            self.note("  -> %s" % str(r)[:90])
        else:
            time.sleep(0.3)
        self.escort.walked(cm, time.monotonic())
        self.busy = ""

    def do_stop(self):
        self.busy = "stopping"
        if not self.dry_run:
            self.robot.tool("stop_robot", {}, timeout=10)
        self.busy = ""

    # ------------------------------------------------------------------
    def feed_look_back(self):
        """If we are turned round waiting on a verdict, go and get one."""
        with self.lock:
            if self.escort.phase is not Phase.LOOKING_BACK:
                return
            aim = wrap180(self.escort.heading_deg
                          + self.escort.pol.look_back_deg)
            if abs(wrap180(self.escort.facing_deg - aim)) > 10.0:
                return                      # needs to turn first
        people, _ = self.observe(time.monotonic())
        with self.lock:
            if self.escort.phase is not Phase.LOOKING_BACK:
                return
            cands = [Candidate(range_m=p.range_m, bearing_deg=p.bearing_deg,
                               height_m=p.height_m,
                               appearance=tuple(p.appearance))
                     for p in people]
            r = self.escort.saw(cands, time.monotonic())
        if r.kind == "say":
            self.pending_say.append(r.text)
            self.note("SAY: %s  (%s)" % (r.text, r.why))

    # ------------------------------------------------------------------
    def loop(self):
        while True:
            now = time.monotonic()
            with self.lock:
                phase = self.escort.phase
            if phase in (Phase.IDLE, Phase.ARRIVED, Phase.ABORTED):
                time.sleep(0.3)
                continue

            people, walk_ok = self.observe(now)
            now = time.monotonic()

            with self.lock:
                a = self.escort.next_action(
                    now, guard_allows_walk=walk_ok,
                    guard_reason=self.last_guard.get("reason", ""))

            if a.kind == "turn":
                self.do_turn(a.bearing_deg, now)
            elif a.kind == "walk":
                self.do_walk(a.distance_cm, now)
            elif a.kind == "say":
                self.pending_say.append(a.text)
                self.note("SAY: %s" % a.text)
                time.sleep(0.3)
            elif a.kind == "stop":
                self.do_stop()
                self.note("stopped: %s" % a.why)
            elif a.kind == "wait":
                time.sleep(min(max(a.seconds, 0.1), 1.0))
            elif a.kind == "done":
                self.note("escort finished: %s" % a.why)
                time.sleep(0.3)

            # Feed the look-back verdict whenever the body is ALREADY pointed
            # backwards - not only just after a turn. The first check needs a
            # turn and the ones after a wait do not, and keying this off the
            # turn alone wedged the machine in looking_back forever: it asked
            # "Still with me?", then sat there waiting for a turn it had no
            # reason to make.
            self.feed_look_back()

    # ------------------------------------------------------------------
    def status(self):
        with self.lock:
            d = self.escort.as_dict()
        d.update({
            "busy": self.busy,
            "dry_run": self.dry_run,
            "pending_say": self.pending_say[-5:],
            "visibility": self.ring.summary(self.escort.heading_deg,
                                            time.monotonic()),
            "guard": {k: self.last_guard.get(k)
                      for k in ("state", "zone", "reason", "walk_ok",
                                "nearest_m", "child_near")},
            "log": self.log[-20:],
            "open_loop_turns": True,
        })
        return d


class _P:
    """guard_core.Person-shaped view of the guard's JSON."""
    def __init__(self, d):
        self.range_m = d["range_m"]
        self.bearing_deg = d["bearing_deg"]
        self.height_m = d["height_m"]
        self.width_deg = d.get("width_deg", 12.0)
        self.appearance = d.get("appearance") or ()


def serve(runner, host, port):
    class H(BaseHTTPRequestHandler):
        def _json(self, code, body):
            raw = json.dumps(body, indent=2).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path.split("?")[0] not in ("/", "/escort", "/status"):
                return self.send_error(404)
            self._json(200, runner.status())

        def do_POST(self):
            path = self.path.split("?")[0]
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad json"})

            if path == "/stop":
                with runner.lock:
                    runner.escort.abort("stopped by request")
                runner.do_stop()
                return self._json(200, runner.status())

            if path != "/start":
                return self.send_error(404)

            cm = float(body.get("distance_cm") or 0)
            if not (10 <= cm <= 2000):
                return self._json(400, {
                    "error": "distance_cm must be 10-2000"})
            g = runner.last_guard or runner.guard.get("/guard?appearance=1") or {}
            # Record the guest NOW, while they are still in front of the camera
            # - the only moment it can see them properly. Nearest person ahead.
            ahead = sorted(
                (p for p in (g.get("people_seen") or [])
                 if abs(p["bearing_deg"]) < 50),
                key=lambda p: p["range_m"])
            who = (Candidate(range_m=ahead[0]["range_m"],
                             bearing_deg=ahead[0]["bearing_deg"],
                             height_m=ahead[0]["height_m"],
                             appearance=tuple(ahead[0].get("appearance") or ()))
                   if ahead else None)
            with runner.lock:
                runner.escort.start(cm, str(body.get("destination") or ""),
                                    follower=who, now=time.monotonic(),
                                    heading_deg=runner.escort.facing_deg)
            runner.note("escort start: %.0f cm to %r, guest %s"
                        % (cm, body.get("destination"),
                           ("%.1f m ahead" % who.range_m) if who
                            else "NOT VISIBLE - will check behind anyway"))
            return self._json(200, runner.status())

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--guard", default="http://127.0.0.1:8092")
    ap.add_argument("--robot", default=os.environ.get("ROBOT_URL",
                                                      "http://127.0.0.1:8090"))
    ap.add_argument("--token", default=os.environ.get("D1_ROBOT_TOKEN", ""))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8093)
    ap.add_argument("--leg-cm", type=float, default=150.0,
                    help="distance between look-back checks. walk_distance "
                         "caps at 200.")
    ap.add_argument("--look-back", type=float, default=135.0,
                    help="degrees to turn for a check. 122 HFOV means 135 puts "
                         "straight-behind well inside the frame; 180 centres "
                         "it but costs half again as much turning.")
    ap.add_argument("--turn-deg-per-sec", type=float, default=43.0,
                    help="MEASURE THIS ON THE ROBOT. move_robot takes a speed "
                         "fraction, not an angle, so turns are open-loop. "
                         "43 is the spec-derived guess for speed=0.25.")
    ap.add_argument("--turn-speed", type=float, default=0.25)
    ap.add_argument("--settle-s", type=float, default=0.6,
                    help="pause after a turn before believing the camera")
    ap.add_argument("--stale-after", type=float, default=3.0)
    ap.add_argument("--follow-max", type=float, default=5.0)
    ap.add_argument("--lagging", type=float, default=3.0)
    ap.add_argument("--wait-s", type=float, default=5.0)
    ap.add_argument("--max-missed", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true",
                    help="decide and log, never command the robot")
    args = ap.parse_args()

    runner = Runner(Peer(args.guard), Peer(args.robot, args.token), args)
    serve(runner, args.host, args.port)
    print("d1-escort  guard=%s  robot=%s  http=%s:%d  leg=%.0fcm%s"
          % (args.guard, args.robot, args.host, args.port, args.leg_cm,
             "  DRY RUN" if args.dry_run else ""), flush=True)
    print("  turns are OPEN-LOOP at %.0f deg/s - measure this before trusting "
          "the bearings" % args.turn_deg_per_sec, flush=True)
    runner.loop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nd1-escort stopped.", flush=True)
