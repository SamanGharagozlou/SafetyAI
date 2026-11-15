"""Test doubles: no weights, network, GPU or camera required."""

import threading

import numpy as np


class FakeBoxes:
    def __init__(self, rows):
        arr = np.asarray(rows, dtype=np.float32).reshape(-1, 6)
        self.xyxy = arr[:, :4]
        self.conf = arr[:, 4]
        self.cls = arr[:, 5]


class FakeResult:
    """Mimics ultralytics Results: rows of (x1, y1, x2, y2, conf, cls)."""

    def __init__(self, rows=()):
        self.boxes = FakeBoxes(list(rows))


class FakeModel:
    """Stands in for UltralyticsDetector."""

    def __init__(self, rows=(), person_ids=(0,), raise_exc=None, used_fallback=False):
        self.rows = list(rows)
        self.person_ids = list(person_ids)
        self.used_class_fallback = used_fallback
        self.raise_exc = raise_exc
        self.calls = 0

    def predict(self, frame):
        self.calls += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        return [FakeResult(self.rows)]


class FakeCapture:
    """
    Scripted cv2.VideoCapture replacement.

    ``script`` is a list of frames; ``None`` entries simulate a read failure.
    """

    def __init__(self, opened=True, script=None, frame=None):
        self._opened = opened
        self._script = list(script) if script is not None else None
        self._frame = frame if frame is not None else np.zeros((48, 64, 3), np.uint8)
        self.released = False

    def isOpened(self):  # noqa: N802 - mirrors OpenCV API
        return self._opened

    def read(self):
        if self._script is None:
            return True, self._frame.copy()
        if not self._script:
            return False, None
        item = self._script.pop(0)
        return (item is not None), item

    def set(self, *_):
        return False

    def get(self, *_):
        return 0.0

    def release(self):
        self.released = True


class ScriptedOpener:
    """open_fn returning a sequence of FakeCapture objects (or raising)."""

    def __init__(self, captures):
        self._captures = list(captures)
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, _config):
        with self.lock:
            self.calls += 1
            item = self._captures.pop(0) if self._captures else FakeCapture(opened=False)
        if isinstance(item, Exception):
            raise item
        return item


class RecordingLogger:
    def __init__(self):
        self.messages = []

    def info(self, msg):
        self.messages.append(('info', msg))

    def warning(self, msg):
        self.messages.append(('warning', msg))

    def error(self, msg):
        self.messages.append(('error', msg))
