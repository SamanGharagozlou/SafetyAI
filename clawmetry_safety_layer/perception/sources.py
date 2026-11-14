"""Frame sources: the robot's RTSP stream, the team detector's annotated MJPEG, a looping video file (the venue
walkthrough, for the mock), or a folder of stills. All keep only the newest frame (no backlog, nothing stored).

    src = make_source(cfg)        # FRAME_SOURCE env overrides cfg["perception"]["source"]
    frame, ts, seq = src.read()   # BGR numpy array (or None), receive time, sequence number
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _cv2():
    try:
        import cv2
    except ImportError as e:
        raise ImportError("frame sources need OpenCV: pip install -e '.[edge]'") from e
    return cv2


class FrameSource:
    name = "none"

    def __init__(self):
        self.frame = None
        self.ts = 0.0
        self.seq = 0
        self.lock = threading.Lock()
        self.stopped = False
        self.error: str | None = None

    def read(self):
        with self.lock:
            return self.frame, self.ts, self.seq

    def _put(self, frame):
        with self.lock:
            self.frame, self.ts = frame, time.time()
            self.seq += 1

    def start(self):
        threading.Thread(target=self._run, daemon=True, name=f"src-{self.name}").start()
        return self

    def _run(self):  # pragma: no cover - overridden
        pass

    def close(self):
        self.stopped = True


class CaptureSource(FrameSource):
    """OpenCV/FFmpeg capture with reconnect (RTSP or the team's MJPEG /stream)."""

    def __init__(self, url: str, name: str):
        super().__init__()
        self.url, self.name = url, name

    def _run(self):
        cv2 = _cv2()
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
        while not self.stopped:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                self.error = f"cannot open {self.url}"
                time.sleep(2.0)
                continue
            self.error = None
            while not self.stopped:
                ok, frame = cap.read()
                if not ok:
                    self.error = "stream lost"
                    break
                self._put(frame)
            cap.release()
            time.sleep(1.0)


def RtspSource(url: str) -> CaptureSource:  # noqa: N802  (reads like a class at call sites)
    return CaptureSource(url, "rtsp")


def MjpegSource(base_url: str) -> CaptureSource:  # noqa: N802
    return CaptureSource(base_url.rstrip("/") + "/stream", "team_mjpeg")


class VideoFileSource(FrameSource):
    """Loops a video file at its own frame rate (the venue walkthrough as the mock camera)."""

    name = "video"

    def __init__(self, path: str | Path, fps: float | None = None):
        super().__init__()
        self.path = str(path)
        self.fps = fps
        self._seek: float | None = None
        self.frozen = False  # mock demo: hold one frame (the walkthrough is not where the twin is)
        self.pos_s = 0.0  # current position in the file
        # timeline hook: on_event(kind, wall=, t=) for "loop" (t = 0), "freeze" (held at t), "play" (resumes at t), so
        # recorded sessions can be aligned with the camera later (scripts/export_cosmos_dataset.py)
        self.on_event = None

    def _ev(self, kind: str, t: float):
        if callable(self.on_event):
            try:
                self.on_event(kind, wall=round(time.time(), 3), t=round(t, 3), path=self.path)
            except Exception:  # noqa: BLE001
                pass

    def freeze(self, t_s: float | None):
        """Show the frame at t_s and hold it (None = play from where it is)."""
        if t_s is None:
            self.frozen = False
            self._ev("play", self.pos_s)
        else:
            self._seek, self.frozen = float(t_s), True
            self._ev("freeze", float(t_s))

    def _run(self):
        cv2 = _cv2()
        while not self.stopped:
            cap = cv2.VideoCapture(self.path)
            if not cap.isOpened():
                self.error = f"cannot open {self.path}"
                time.sleep(2.0)
                continue
            fps = self.fps or cap.get(cv2.CAP_PROP_FPS) or 25.0
            if not self.frozen:
                self._ev("loop", 0.0)
            while not self.stopped:
                t0 = time.time()
                if self._seek is not None:
                    cap.set(cv2.CAP_PROP_POS_MSEC, self._seek * 1000)
                    self._seek = None
                    ok, frame = cap.read()
                    if ok:
                        self._put(frame)
                if self.frozen:
                    with self.lock:
                        self.ts = time.time()  # the held frame stays "current"
                        self.seq += 1
                    time.sleep(1.0 / fps)
                    continue
                ok, frame = cap.read()
                if not ok:
                    break  # loop
                self.pos_s = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                self._put(frame)
                time.sleep(max(0.0, 1.0 / fps - (time.time() - t0)))
            cap.release()


class StillSource(FrameSource):
    """Cycles through the JPEG/PNG stills in a folder (docs/venue/*.jpg), period_s each."""

    name = "stills"

    def __init__(self, folder: str | Path, period_s: float = 2.0, files: list[Path] | None = None):
        super().__init__()
        self.folder = Path(folder)
        self.period_s = period_s
        self.files = files

    def list(self) -> list[Path]:
        if self.files is not None:
            return list(self.files)
        return sorted(p for p in self.folder.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))

    def _run(self):
        cv2 = _cv2()
        while not self.stopped:
            files = self.list()
            if not files:
                self.error = f"no stills in {self.folder}"
                time.sleep(2.0)
                continue
            for p in files:
                if self.stopped:
                    break
                img = cv2.imread(str(p))
                if img is not None:
                    self._put(img)
                time.sleep(self.period_s)


class NullSource(FrameSource):
    name = "none"

    def start(self):
        return self


def make_source(cfg: dict) -> FrameSource:
    pc = cfg.get("perception") or {}
    kind = os.environ.get("FRAME_SOURCE") or pc.get("source", "none")

    def rel(p):
        q = Path(p)
        return q if q.is_absolute() else ROOT / q

    if kind == "team":
        return MjpegSource(os.environ.get("TEAM_DETECTOR_URL") or pc.get("team_detector_url", "http://127.0.0.1:8090"))
    if kind == "rtsp":
        return RtspSource(os.environ.get("ROBOT_RTSP") or pc.get("rtsp", "rtsp://172.24.1.41:8554/test"))
    if kind == "video":
        return VideoFileSource(
            rel(os.environ.get("FRAME_VIDEO") or pc.get("video", "assets/venue/spa_walkthrough.mp4"))
        )
    if kind == "stills":
        return StillSource(rel(pc.get("stills", "docs/venue")))
    if kind == "none":
        return NullSource()
    raise ValueError(f"FRAME_SOURCE must be team | rtsp | video | stills | none, not {kind!r}")
