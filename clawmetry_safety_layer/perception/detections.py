"""Object / person detections.

TeamDetectorClient: polls the SafetyAI team's detector (perception/detect.py on their PC) at
http://<team-pc>:8090/latest.json, 5 Hz, and reports staleness. Their record:
    {"ts", "frame", "fps", "width", "height", "detections": [{"class", "conf", "box": [x1, y1, x2, y2]}]}
LocalYolo: optional CPU fallback (ultralytics yolo11n, `pip install -e '.[yolo]'`) when their detector is down.
Neither stores frames.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Any

STALE_S = 1.0  # detections older than this are not evidence of anything now


class TeamDetectorClient:
    name = "team"

    def __init__(self, base_url: str, hz: float = 5.0, stale_s: float = STALE_S, fetch=None):
        self.url = base_url.rstrip("/") + "/latest.json"
        self.period = 1.0 / hz
        self.stale_s = stale_s
        self.latest: dict | None = None
        self.t_recv = 0.0
        self.error: str | None = None
        self._fetch = fetch or self._http
        self._stop = threading.Event()
        self._last_frame = None
        self._t_frame_change = 0.0

    def _http(self) -> dict:
        with urllib.request.urlopen(self.url, timeout=1.0) as r:
            return json.loads(r.read())

    def poll_once(self) -> None:
        try:
            d = self._fetch()
        except Exception as e:  # noqa: BLE001  (down / slow detector = stale, never an exception upstream)
            self.error = f"{e.__class__.__name__}: {e}"[:200]
            return
        if not isinstance(d, dict) or "detections" not in d:
            self.error = "no detections in /latest.json"
            return
        now = time.time()
        if d.get("frame") != self._last_frame:  # a frozen detector keeps serving the same frame: stale too
            self._last_frame = d.get("frame")
            self._t_frame_change = now
        self.latest, self.t_recv, self.error = d, now, None

    def start(self):
        def loop():
            while not self._stop.is_set():
                t0 = time.time()
                self.poll_once()
                self._stop.wait(max(0.0, self.period - (time.time() - t0)))

        threading.Thread(target=loop, daemon=True, name="team-detector").start()
        return self

    def close(self):
        self._stop.set()

    def age_s(self) -> float | None:
        if self.latest is None:
            return None
        return time.time() - min(self.t_recv, self._t_frame_change or self.t_recv)

    def get(self) -> tuple[list[dict], dict, bool]:
        """(detections, meta {width, height, fps}, stale)."""
        d = self.latest
        age = self.age_s()
        stale = d is None or age is None or age > self.stale_s
        if d is None:
            return [], {}, True
        meta = {k: d.get(k) for k in ("width", "height", "fps", "frame")}
        return list(d.get("detections") or []), meta, stale


class LocalYolo:
    """CPU fallback on our own frames. Import is lazy: without ultralytics, available() is False."""

    name = "local_yolo"

    def __init__(self, model: str = "yolo11n.pt", conf: float = 0.4, classes=("person",)):
        self.model_name, self.conf, self.classes = model, conf, set(classes)
        self.model: Any = None
        self.error: str | None = None

    def available(self) -> bool:
        if self.model is not None:
            return True
        try:
            from ultralytics import YOLO

            self.model = YOLO(os.path.expanduser(self.model_name))
            return True
        except Exception as e:  # noqa: BLE001
            self.error = f"{e.__class__.__name__}: {e}"[:200]
            return False

    def detect(self, frame) -> list[dict]:
        if not self.available():
            return []
        r = self.model.predict(frame, conf=self.conf, imgsz=640, device="cpu", verbose=False)[0]
        names = self.model.names
        out = []
        for (x1, y1, x2, y2), c, k in zip(
            r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist(), strict=False
        ):
            cls = names[int(k)]
            if not self.classes or cls in self.classes:
                out.append(
                    {"class": cls, "conf": round(float(c), 3), "box": [round(x1), round(y1), round(x2), round(y2)]}
                )
        return out
