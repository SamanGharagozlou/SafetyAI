#!/usr/bin/env python3
"""
d1-guard - watches the robot's camera and stops it walking into people.

Runs ON THE JETSON, next to the bridge. Two jobs:

  1. PUBLISH a verdict at :8092/guard, so the console and the voice tools can
     refuse to START a move that is already unsafe.
  2. INTERRUPT a move already in flight, by sending stop_robot.

(2) is not belt-and-braces, it is the main event. `walk_distance` BLOCKS on the
robot for up to 25 s: by the time a guest steps in front of the dog, the walk
command has long since been accepted and nobody is asking permission any more.
Refusing the next command would be useless. Something has to interrupt this one.

WHY NOT IN THE BRIDGE: the bridge and d1-vapi-server are not in this repo, so
this is deliberately an outboard process that needs no edit to code we cannot
see. The cost is honest: an override that races the walk, rather than a gate the
walk cannot get past. If this graduates past the demo, the gate belongs inside
the agent, before cmd_vel is published.

    python3 guard.py --rtsp rtsp://192.168.168.168:8554/test \
                     --robot http://127.0.0.1:8090 --token "$D1_ROBOT_TOKEN"

Needs opencv (JetPack ships it) and ultralytics for the detector. With neither,
it still starts and reports FREEZE forever - which is the correct thing for a
safety service with no eyes to do, and is why --allow-blind exists for anyone
who wants the robot usable without it.
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
from guard_core import Camera, Detection, Guard, Policy, State    # noqa: E402

# Vendor doc 5.1: wired link. Wireless is 192.168.234.1.
DEFAULT_RTSP = "rtsp://192.168.168.168:8554/test"

# Low-latency pull, straight from the vendor's own example (5.1.5). The buffer
# settings are the difference between reacting to now and reacting to two
# seconds ago, which for a safety loop is the difference between working and not.
GST = ("rtspsrc location={url} latency=0 ! rtph264depay ! h264parse ! "
       "avdec_h264 ! videoconvert ! video/x-raw,format=BGR ! "
       "appsink sync=false drop=true max-buffers=1")


class Frames:
    """Newest frame only. A safety loop must never work through a backlog."""

    def __init__(self, url, width, height):
        self.url, self.width, self.height = url, width, height
        self._frame = None
        self._t = 0.0
        self._lock = threading.Lock()
        self.ok = False
        self.error = "not started"
        self.frames = 0

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def latest(self):
        with self._lock:
            return self._frame, self._t

    def _run(self):
        try:
            import cv2
        except ImportError:
            self.error = "opencv not installed"
            return
        backoff = 1.0
        while True:
            cap = cv2.VideoCapture(GST.format(url=self.url), cv2.CAP_GSTREAMER)
            if not cap.isOpened():       # fall back to plain RTSP over TCP
                cap = cv2.VideoCapture(self.url + "?tcp")
            if not cap.isOpened():
                self.ok = False
                self.error = "cannot open %s" % self.url
                time.sleep(backoff)
                backoff = min(backoff * 2, 15.0)
                continue
            self.ok, self.error, backoff = True, "", 1.0
            while True:
                got, frame = cap.read()
                if not got:
                    self.ok, self.error = False, "stream dropped"
                    break
                with self._lock:
                    self._frame, self._t = frame, time.monotonic()
                self.frames += 1
            cap.release()


class People:
    """Person boxes from a frame. Swappable; the safety logic does not care."""

    def __init__(self, weights="yolov8n.pt", conf=0.35, imgsz=640):
        self.conf, self.imgsz = conf, imgsz
        self.model = None
        self.error = "not loaded"
        try:
            from ultralytics import YOLO
            self.model = YOLO(weights)
            self.error = ""
        except ImportError:
            self.error = "ultralytics not installed"
        except Exception as exc:                        # bad weights, no CUDA...
            self.error = "detector failed to load: %s" % exc

    @property
    def ready(self):
        return self.model is not None

    def __call__(self, frame):
        # classes=[0] is COCO 'person'. Everything else in the frame is scenery
        # as far as this service is concerned.
        res = self.model.predict(frame, conf=self.conf, imgsz=self.imgsz,
                                 classes=[0], verbose=False)
        out = []
        for r in res:
            for b in r.boxes:
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
                out.append(Detection(x1, y1, x2, y2, float(b.conf[0])))
        return out


class Robot:
    """Just enough of the robot API to shout stop."""

    def __init__(self, base, token, dry_run=False):
        self.base = base.rstrip("/")
        self.token = token
        self.dry_run = dry_run
        self.stops = 0
        self.last_error = ""

    def stop(self):
        self.stops += 1
        if self.dry_run:
            return True
        body = json.dumps({"message": {"type": "tool-calls", "toolCallList": [
            {"id": "guard", "name": "stop_robot", "arguments": {}}]}}).encode()
        req = urllib.request.Request(
            self.base + "/vapi", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % self.token})
        try:
            # Short timeout on purpose: a stop that has not landed in 3 s has
            # failed, and the right response is to send another one, not to wait.
            with urllib.request.urlopen(req, timeout=3.0):
                return True
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            self.last_error = str(exc)
            return False


def serve(guard, state, port, host):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            from urllib.parse import parse_qs, urlparse
            u = urlparse(self.path)
            if u.path not in ("/guard", "/", "/status"):
                self.send_error(404)
                return
            q = parse_qs(u.query)
            v = guard.tick(time.monotonic())
            body = dict(v.as_dict(), **state())

            # Adjudicate a specific action, so callers never have to reimplement
            # the gesture rules. One source of truth for which gestures lift a
            # paw; a second copy in the console would drift from this one.
            action = (q.get("action") or [None])[0]
            if action:
                name = (q.get("name") or [""])[0]
                if action in ("stop", "status"):
                    ok = True            # never gate a stop
                elif action in ("walk", "move"):
                    ok = v.allows_walk()
                elif action == "gesture":
                    ok = v.allows_gesture(name)
                else:
                    ok = v.allows_walk() and v.allows_gesture(name)
                body["action"] = action
                body["name"] = name
                body["allowed"] = ok
            raw = json.dumps(body, indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rtsp", default=os.environ.get("D1_RTSP", DEFAULT_RTSP))
    ap.add_argument("--robot", default=os.environ.get("ROBOT_URL",
                                                      "http://127.0.0.1:8090"))
    ap.add_argument("--token", default=os.environ.get("D1_ROBOT_TOKEN", ""))
    ap.add_argument("--port", type=int, default=8092)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--weights", default="yolov8n.pt")
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--cam-height", type=float, default=0.40)
    ap.add_argument("--cam-tilt", type=float, default=12.0)
    ap.add_argument("--calibrated", action="store_true",
                    help="set once calibrate.py numbers are in. Without it the "
                         "guard caps the robot to slow, because an uncalibrated "
                         "ground plane reports confident nonsense.")
    ap.add_argument("--dry-run", action="store_true",
                    help="decide and log, never actually send stop")
    ap.add_argument("--allow-blind", action="store_true",
                    help="if the camera or detector is unavailable, report GO "
                         "instead of FREEZE. The robot is then unguarded - this "
                         "exists so a missing dependency cannot brick the demo, "
                         "and it should be off whenever the public is present.")
    args = ap.parse_args()

    frames = Frames(args.rtsp, 1280, 720).start()
    det = People(args.weights, args.conf)
    cam = Camera(height_m=args.cam_height, tilt_deg=args.cam_tilt,
                 calibrated=args.calibrated)
    guard = Guard(cam, Policy())
    robot = Robot(args.robot, args.token, args.dry_run)

    stats = {"detect_ms": 0.0, "ticks": 0, "overrides": 0}

    def extra():
        return {"camera": {"ok": frames.ok, "error": frames.error,
                           "frames": frames.frames, "rtsp": args.rtsp},
                "detector": {"ready": det.ready, "error": det.error,
                             "ms": round(stats["detect_ms"], 1)},
                "blind_radius_m": round(cam.blind_radius_m, 2),
                "calibrated": cam.calibrated,
                "dry_run": args.dry_run,
                "allow_blind": args.allow_blind,
                "stops_sent": robot.stops,
                "overrides": stats["overrides"]}

    serve(guard, extra, args.port, args.host)
    print("d1-guard  camera=%s  robot=%s  http=%s:%d  blind<%.2fm%s"
          % (args.rtsp, args.robot, args.host, args.port, cam.blind_radius_m,
             "  DRY RUN" if args.dry_run else ""), flush=True)
    if not det.ready:
        print("  detector unavailable: %s" % det.error, flush=True)
        print("  -> %s" % ("reporting GO, robot is UNGUARDED (--allow-blind)"
                           if args.allow_blind else
                           "reporting FREEZE until it loads"), flush=True)

    blind_mode = args.allow_blind and not det.ready
    period = 1.0 / max(args.hz, 1.0)
    last_state, last_log = None, 0.0

    while True:
        t0 = time.monotonic()
        frame, ft = frames.latest()
        now = time.monotonic()

        if blind_mode:
            v = guard.update([], now)          # nothing seen, nothing claimed
        elif frame is not None and det.ready and now - ft <= 0.5:
            d0 = time.monotonic()
            try:
                boxes = det(frame)
            except Exception as exc:           # a detector crash is a blind guard
                det.error = "detect failed: %s" % exc
                boxes = None
            stats["detect_ms"] = (time.monotonic() - d0) * 1000
            # A thrown detector must NOT look like an empty frame. Empty means
            # "I looked and nobody is there"; a crash means "I did not look".
            v = guard.update(boxes, now) if boxes is not None else guard.tick(now)
        else:
            v = guard.tick(now)                # no frame -> stale -> FREEZE

        stats["ticks"] += 1

        # Interrupt whatever is in flight. Repeated every tick while unsafe,
        # because the walk the robot is executing will happily resume otherwise,
        # and because a velocity controller obeys whichever command came last.
        if v.state in (State.HOLD, State.FREEZE):
            if robot.stop():
                stats["overrides"] += 1

        if v.state != last_state or now - last_log > 10.0:
            print("[%7.1f] %-6s %-7s %s" % (now, v.state.name, v.zone.name,
                                            v.reason), flush=True)
            last_state, last_log = v.state, now

        time.sleep(max(0.0, period - (time.monotonic() - t0)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nd1-guard stopped - the robot is now unguarded.", flush=True)
