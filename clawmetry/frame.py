"""Grab one small JPEG from the robot camera, for the world-model judge.

PRIVACY. This produces an image of a real venue with real guests in it, so the
rules from clawmetry_safety_layer/docs/PRIVACY.md are enforced here rather than
trusted to the caller:

  * at most 640x360, quality 70 - enough for "is that a child / is the floor
    wet", far too little to identify anyone comfortably;
  * held in memory only. Never written to disk, never put in the ledger, never
    logged. Only the verdict text is recorded;
  * refuses outright in a `camera_off` zone (pool_deck, relax_level in
    policies/zones_therme.yaml), where no frame may be read at all;
  * ffmpeg is not installed on the Orin, so this uses OpenCV, which ships with
    JetPack. The import is guarded so the collector still runs without it.

The first few reads after an RTSP connect are usually empty, so it retries
rather than reporting "no camera" on a stream that is fine.
"""

from __future__ import annotations

import os
import threading
import time

CAMERA_OFF_ZONES = {"pool_deck", "relax_level", "changing", "sauna"}
MAX_W, MAX_H, QUALITY = 640, 360, 70


class FrameGrabber:
    def __init__(self, rtsp: str = "", camera_off_zones=None, max_age_s: float = 2.0):
        self.rtsp = rtsp or os.environ.get("ROBOT_RTSP", "")
        self.off = set(camera_off_zones or CAMERA_OFF_ZONES)
        self._cv2 = None
        self.last_error = ""
        # Opening RTSP per judge call measured 8.35 s end to end, which is far
        # too slow to sit in front of a gesture. A warm frame makes the judge
        # cost the judge's own latency and nothing else. Only the newest frame
        # is ever held, and it is dropped on camera_off - same rule as before.
        self.max_age_s = max_age_s
        self._latest: bytes = b""
        self._latest_ts: float = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        """Begin keeping a warm frame. Safe to call when unconfigured."""
        if not self.configured or self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            jpeg = self._capture()
            if jpeg:
                with self._lock:
                    self._latest, self._latest_ts = jpeg, time.time()
            time.sleep(1.0)

    def cached(self, zone: str = "") -> tuple[bytes, float]:
        """Newest warm frame and its age, or (b'', -1) if there is none."""
        if zone and zone in self.off:
            return b"", -1.0
        with self._lock:
            if not self._latest:
                return b"", -1.0
            return self._latest, time.time() - self._latest_ts

    @property
    def configured(self) -> bool:
        return bool(self.rtsp)

    def _load(self):
        if self._cv2 is None:
            try:
                import cv2  # noqa: PLC0415 - optional, guarded on purpose
                os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS",
                                      "rtsp_transport;tcp")
                self._cv2 = cv2
            except ImportError:
                self._cv2 = False
        return self._cv2

    def grab(self, zone: str = "") -> bytes:
        """One JPEG, or b'' with `last_error` set. Never raises, never stores.

        Prefers the warm frame; falls back to opening the stream.
        """
        self.last_error = ""
        if zone and zone in self.off:
            self.last_error = f"camera_off zone ({zone}): no frame may be read here"
            return b""
        if not self.configured:
            self.last_error = "no ROBOT_RTSP configured"
            return b""
        warm, age = self.cached(zone)
        if warm and 0 <= age <= self.max_age_s:
            return warm
        return self._capture()

    def _capture(self) -> bytes:
        cv2 = self._load()
        if not cv2:
            self.last_error = "opencv not available in this interpreter"
            return b""
        if not self.rtsp:
            self.last_error = "no ROBOT_RTSP configured"
            return b""
        cap = None
        try:
            cap = cv2.VideoCapture(self.rtsp, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                self.last_error = "could not open the RTSP stream"
                return b""
            for _ in range(30):
                ok, frame = cap.read()
                if ok and frame is not None:
                    small = cv2.resize(frame, (MAX_W, MAX_H))
                    ok, buf = cv2.imencode(".jpg", small,
                                           [int(cv2.IMWRITE_JPEG_QUALITY), QUALITY])
                    if ok:
                        return buf.tobytes()
            self.last_error = "stream opened but produced no usable frame"
            return b""
        except Exception as e:                      # a dead camera must not stop the gate
            self.last_error = f"{type(e).__name__}: {e}"
            return b""
        finally:
            if cap is not None:
                cap.release()
