"""Frame sampler: 2-5 fps, <= 640x360 JPEG, POSTed to the brain relay with the pose and the current intent.
Never written to disk; dropped (not queued) while the previous frame is still in flight; nothing at all in a
camera_off zone (docs/PRIVACY.md).

POST {BRAIN_URL}/frame   headers X-Api-Key: BRAIN_API_KEY
     multipart: frame (image/jpeg), meta (JSON: {ts, seq, pose: [x, y, yaw], zone, intent, reason})
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
import uuid


def to_jpeg(bgr, max_w: int = 640, max_h: int = 360, quality: int = 70) -> bytes:
    import cv2

    h, w = bgr.shape[:2]
    s = min(1.0, max_w / w, max_h / h)
    if s < 1.0:
        bgr = cv2.resize(bgr, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise ValueError("JPEG encode failed")
    return buf.tobytes()


def multipart(fields: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    parts = []
    for name, (ctype, data) in fields.items():
        fn = '; filename="frame.jpg"' if ctype == "image/jpeg" else ""
        parts.append(
            f'--{b}\r\nContent-Disposition: form-data; name="{name}"{fn}\r\nContent-Type: {ctype}\r\n\r\n'.encode()
            + data
            + b"\r\n"
        )
    return b"".join(parts) + f"--{b}--\r\n".encode(), f"multipart/form-data; boundary={b}"


class FrameSampler:
    def __init__(self, brain_url: str, api_key: str, fps: float = 2.0, max_w=640, max_h=360, quality=70, post=None):
        self.url = brain_url.rstrip("/") + "/frame"
        self.key = api_key
        self.period = 1.0 / max(0.2, min(5.0, fps))  # 2-5 fps by design; 0.2 floor for tests
        self.max_w, self.max_h, self.quality = max_w, max_h, quality
        self._post = post or self._http
        self._busy = threading.Lock()
        self._next = 0.0
        self.sent = 0
        self.dropped = 0
        self.errors = 0
        self.last_error: str | None = None
        self.last_rtt_s: float | None = None

    def _http(self, body: bytes, ctype: str) -> dict:
        req = urllib.request.Request(
            self.url, data=body, method="POST", headers={"content-type": ctype, "x-api-key": self.key}
        )
        with urllib.request.urlopen(req, timeout=10.0) as r:
            return json.loads(r.read() or b"{}")

    def offer(self, bgr, meta: dict, camera_off: bool = False, force: bool = False) -> bool:
        """Maybe send this frame (rate limit, one in flight). Returns True if a send was started."""
        now = time.time()
        if camera_off or bgr is None:
            return False
        if not force and now < self._next:
            return False
        if not self._busy.acquire(blocking=False):
            self.dropped += 1
            return False
        self._next = now + self.period
        try:
            jpeg = to_jpeg(bgr, self.max_w, self.max_h, self.quality)
        except Exception as e:  # noqa: BLE001
            self._busy.release()
            self.last_error = str(e)
            return False
        threading.Thread(target=self._send, args=(jpeg, dict(meta)), daemon=True, name="frame-sampler").start()
        return True

    def _send(self, jpeg: bytes, meta: dict):
        t0 = time.time()
        try:
            body, ctype = multipart(
                {"frame": ("image/jpeg", jpeg), "meta": ("application/json", json.dumps(meta).encode())}
            )
            self._post(body, ctype)
            self.sent += 1
            self.last_rtt_s = time.time() - t0
            self.last_error = None
        except Exception as e:  # noqa: BLE001  (brain down = judge unavailable upstream; never raises here)
            self.errors += 1
            self.last_error = f"{e.__class__.__name__}: {e}"[:200]
        finally:
            del jpeg  # the only copy; nothing is kept
            self._busy.release()
