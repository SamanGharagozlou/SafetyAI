"""
Person filtering and the ROS-free detection pipeline.

The pipeline wraps any object exposing ``predict(frame) -> result`` where
``result`` looks like an Ultralytics ``Results`` (``boxes.xyxy``, ``boxes.conf``,
``boxes.cls``).  Unit tests substitute a fake model; production uses
:class:`human_perception.model_backend.UltralyticsDetector`.
"""

from dataclasses import dataclass, field
import time
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

PERSON_CLASS_NAME = 'person'
# COCO index of "person".  Used ONLY when a model exposes no class names; any
# model that does expose names must contain "person" or loading fails.
COCO_PERSON_FALLBACK_ID = 0


class PersonClassError(ValueError):
    """Raised when the model's class list contains no 'person' class."""


class InferenceError(RuntimeError):
    """Raised when the model fails on a frame (wraps the original error)."""


@dataclass(frozen=True)
class PersonDetection:
    """One detected person in pixel coordinates of the input image."""

    x1: float
    y1: float
    x2: float
    y2: float
    score: float
    class_index: int

    @property
    def center(self) -> Tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    @property
    def size(self) -> Tuple[float, float]:
        return (self.x2 - self.x1, self.y2 - self.y1)


@dataclass
class FrameResult:
    """Output of the pipeline for one frame."""

    detections: List[PersonDetection] = field(default_factory=list)
    inference_s: float = 0.0
    image_width: int = 0
    image_height: int = 0

    @property
    def count(self) -> int:
        return len(self.detections)

    @property
    def present(self) -> bool:
        return bool(self.detections)


def resolve_person_class_ids(names: Optional[Any]) -> Tuple[List[int], bool]:
    """
    Find the class index/indices named ``person``.

    Returns ``(ids, used_fallback)``.  ``names`` may be a dict ``{id: name}``
    (Ultralytics) or a list.  When ``names`` is missing/empty the documented
    COCO fallback ``[0]`` is returned with ``used_fallback=True``.
    """
    if not names:
        return [COCO_PERSON_FALLBACK_ID], True
    items: Iterable[Tuple[Any, Any]]
    items = names.items() if isinstance(names, Mapping) else enumerate(names)
    ids = sorted(int(k) for k, v in items if str(v).strip().lower() == PERSON_CLASS_NAME)
    if not ids:
        raise PersonClassError(
            "model class list has no 'person' class; this node detects people only. "
            'Use a COCO-pretrained model such as yolo26n.pt.')
    return ids, False


def _to_numpy(value: Any) -> np.ndarray:
    if value is None:
        return np.zeros((0,), dtype=np.float32)
    if hasattr(value, 'detach'):
        value = value.detach()
    if hasattr(value, 'cpu'):
        value = value.cpu()
    if hasattr(value, 'numpy'):
        value = value.numpy()
    return np.asarray(value, dtype=np.float32)


def extract_person_detections(result: Any, person_ids: Sequence[int],
                              confidence_threshold: float,
                              image_shape: Optional[Tuple[int, int]] = None
                              ) -> List[PersonDetection]:
    """
    Filter a raw detector result down to qualifying person detections.

    Filtering is applied here even if the model was asked for person classes
    only, so behaviour does not depend on backend-specific class filtering.
    Boxes are clipped to the image when ``image_shape=(h, w)`` is given and
    degenerate (zero-area) boxes are discarded.
    """
    boxes = getattr(result, 'boxes', None)
    if boxes is None:
        return []
    xyxy = _to_numpy(getattr(boxes, 'xyxy', None)).reshape(-1, 4)
    conf = _to_numpy(getattr(boxes, 'conf', None)).reshape(-1)
    cls = _to_numpy(getattr(boxes, 'cls', None)).reshape(-1)
    if not (len(xyxy) == len(conf) == len(cls)):
        raise InferenceError(
            f'inconsistent detector output: {len(xyxy)} boxes, {len(conf)} scores, '
            f'{len(cls)} classes')
    wanted = set(int(i) for i in person_ids)
    # Compare in the detector's float32 precision: float32(0.45) < float64(0.45), which
    # would otherwise silently drop detections sitting exactly on the threshold.
    threshold = np.float32(confidence_threshold)
    out: List[PersonDetection] = []
    for (x1, y1, x2, y2), score, c in zip(xyxy, conf, cls):
        if int(c) not in wanted or not np.isfinite(score) or score < threshold:
            continue
        if image_shape is not None:
            h, w = image_shape
            x1, x2 = float(np.clip(x1, 0, w)), float(np.clip(x2, 0, w))
            y1, y2 = float(np.clip(y1, 0, h)), float(np.clip(y2, 0, h))
        if not np.all(np.isfinite([x1, y1, x2, y2])) or x2 <= x1 or y2 <= y1:
            continue
        out.append(PersonDetection(float(x1), float(y1), float(x2), float(y2),
                                   float(score), int(c)))
    out.sort(key=lambda d: d.score, reverse=True)
    return out


class DetectionPipeline:
    """Validate a frame, run the model once, return filtered person detections."""

    def __init__(self, model: Any, person_ids: Sequence[int],
                 confidence_threshold: float, clock=time.perf_counter) -> None:
        self._model = model
        self._person_ids = list(person_ids)
        self._conf = confidence_threshold
        self._clock = clock

    def process(self, frame: np.ndarray) -> FrameResult:
        if (not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3
                or frame.dtype != np.uint8 or frame.size == 0):
            raise ValueError('frame must be a non-empty HxWx3 uint8 array')
        h, w = frame.shape[:2]
        start = self._clock()
        try:
            raw = self._model.predict(frame)
        except Exception as exc:  # noqa: B902 - any backend error is reported uniformly
            raise InferenceError(f'{type(exc).__name__}: {exc}') from exc
        elapsed = self._clock() - start
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        dets = extract_person_detections(raw, self._person_ids, self._conf, (h, w))
        return FrameResult(dets, elapsed, w, h)
