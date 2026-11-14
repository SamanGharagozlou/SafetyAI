#!/usr/bin/env python3
"""Object and person detection on the AgiBot D1 camera stream.

Outputs:
  - Live window with boxes (cv2; turn off with --no-show)
  - Browser stream with boxes at http://localhost:<port>/  (MJPEG)
    latest detections as JSON at http://localhost:<port>/latest.json
  - One JSON line per processed frame on stdout (or --json FILE)
"""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
import cv2  # noqa: E402
from ultralytics import YOLO  # noqa: E402


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def window_available():
    """Qt aborts the whole process if it can't open a window, so probe in a child process."""
    import subprocess
    probe = "import cv2; cv2.namedWindow('probe'); cv2.waitKey(1); cv2.destroyAllWindows()"
    try:
        return subprocess.run([sys.executable, "-c", probe], capture_output=True, timeout=15).returncode == 0
    except subprocess.TimeoutExpired:
        return False


class LatestFrameReader:
    """Reads the stream in a background thread and keeps only the newest frame,
    so inference always works on current video instead of a growing backlog."""

    def __init__(self, src):
        self.src = src
        self.frame = None
        self.seq = 0
        self.cond = threading.Condition()
        self.stopped = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self.stopped:
            cap = cv2.VideoCapture(self.src, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                log(f"[reader] cannot open {self.src}, retrying in 2s")
                time.sleep(2)
                continue
            log(f"[reader] connected to {self.src}")
            while not self.stopped:
                ok, frame = cap.read()
                if not ok:
                    log("[reader] stream lost, reconnecting")
                    break
                with self.cond:
                    self.frame = frame
                    self.seq += 1
                    self.cond.notify_all()
            cap.release()
            time.sleep(1)

    def get(self, last_seq, timeout=5.0):
        with self.cond:
            self.cond.wait_for(lambda: self.seq != last_seq or self.stopped, timeout)
            return self.frame, self.seq


class MjpegHub:
    """Holds the latest annotated JPEG and detections for the HTTP server."""

    def __init__(self):
        self.jpeg = None
        self.dets = {}
        self.seq = 0
        self.cond = threading.Condition()

    def publish(self, jpeg, dets):
        with self.cond:
            self.jpeg, self.dets = jpeg, dets
            self.seq += 1
            self.cond.notify_all()


PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>Robot detection</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;background:#111;color:#ddd;font:14px sans-serif}
img{display:block;max-width:100%;height:auto;margin:auto}pre{padding:8px;white-space:pre-wrap}</style>
</head><body><img src="/stream"><pre id="d"></pre><script>
setInterval(async()=>{try{const r=await fetch('/latest.json');const j=await r.json();
document.getElementById('d').textContent=(j.detections||[]).map(x=>x.class+' '+x.conf.toFixed(2)).join('\\n')}catch(e){}},500)
</script></body></html>"""


def make_handler(hub):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, "text/html", PAGE)
            elif self.path == "/latest.json":
                with hub.cond:
                    body = json.dumps(hub.dets).encode()
                self._send(200, "application/json", body)
            elif self.path == "/stream":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                seq = -1
                try:
                    while True:
                        with hub.cond:
                            hub.cond.wait_for(lambda: hub.seq != seq, timeout=5)
                            jpeg, seq = hub.jpeg, hub.seq
                        if jpeg is None:
                            continue
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self._send(404, "text/plain", b"not found")

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="rtsp://172.24.1.41:8554/test")
    ap.add_argument("--model", default="yolo11s.pt", help="any Ultralytics model, e.g. yolo11n.pt / yolo11m.pt")
    ap.add_argument("--conf", type=float, default=0.4)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--classes", default="", help="comma-separated class names to keep, e.g. person,dog")
    ap.add_argument("--port", type=int, default=8090, help="browser stream port (0 disables)")
    ap.add_argument("--json", default="-", help="file for per-frame JSON lines ('-' = stdout, '' = off)")
    ap.add_argument("--no-show", action="store_true", help="don't open a local window")
    ap.add_argument("--jpeg-quality", type=int, default=80)
    args = ap.parse_args()

    model = YOLO(args.model)
    names = model.names
    class_ids = None
    if args.classes:
        wanted = {c.strip() for c in args.classes.split(",") if c.strip()}
        class_ids = [i for i, n in names.items() if n in wanted]
        unknown = wanted - {names[i] for i in class_ids}
        if unknown:
            sys.exit(f"unknown classes: {', '.join(sorted(unknown))}")

    json_out = None
    if args.json == "-":
        json_out = sys.stdout
    elif args.json:
        json_out = open(args.json, "a", buffering=1)

    hub = MjpegHub()
    if args.port:
        srv = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(hub))
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log(f"[http] browser stream: http://localhost:{args.port}/")

    show = not args.no_show
    if show and not window_available():
        log("[show] can't open a window (on WSL: sudo apt install libsm6 libice6); "
            "use the browser stream instead")
        show = False
    reader = LatestFrameReader(args.source)
    seq, n, t0, fps = 0, 0, time.time(), 0.0
    try:
        while True:
            frame, seq = reader.get(seq)
            if frame is None:
                continue
            r = model.predict(frame, conf=args.conf, imgsz=args.imgsz, classes=class_ids,
                              device=0, verbose=False)[0]

            dets = []
            for (x1, y1, x2, y2), c, k in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(),
                                              r.boxes.cls.tolist()):
                dets.append({"class": names[int(k)], "conf": round(c, 3),
                             "box": [round(x1), round(y1), round(x2), round(y2)]})
            n += 1
            now = time.time()
            if now - t0 >= 1:
                fps, n, t0 = n / (now - t0), 0, now
            record = {"ts": round(now, 3), "frame": seq, "fps": round(fps, 1),
                      "width": frame.shape[1], "height": frame.shape[0], "detections": dets}
            if json_out:
                json_out.write(json.dumps(record) + "\n")

            annotated = r.plot()
            cv2.putText(annotated, f"{fps:.1f} fps", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1,
                        (0, 255, 0), 2)
            if args.port:
                ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality])
                if ok:
                    hub.publish(buf.tobytes(), record)
            if show:
                try:
                    cv2.imshow("robot detection (q to quit)", annotated)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                except cv2.error as e:
                    log(f"[show] window unavailable ({e.msg.strip()}); continuing without it")
                    show = False
    except KeyboardInterrupt:
        pass
    finally:
        reader.stopped = True
        if show:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
