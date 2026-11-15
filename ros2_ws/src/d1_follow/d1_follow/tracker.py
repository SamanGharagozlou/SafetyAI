from dataclasses import dataclass, field

import numpy as np


def iou(a, b) -> float:
    """Intersection over union of two xyxy boxes."""
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = float(iw) * float(ih)
    union = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / float(union) if union > 0 else 0.0


@dataclass
class Track:
    id: int
    box: np.ndarray
    score: float
    hits: int = 1
    misses: int = 0
    min_hits: int = 3
    vel: np.ndarray = field(default_factory=lambda: np.zeros(4, dtype=np.float32))

    @property
    def confirmed(self) -> bool:
        return self.hits >= self.min_hits

    @property
    def visible(self) -> bool:
        return self.misses == 0


class IouTracker:
    """Greedy IoU tracker with a constant-velocity box prediction."""

    def __init__(self, iou_min=0.3, min_hits=3, max_misses=15):
        self.iou_min = iou_min
        self.min_hits = min_hits
        self.max_misses = max_misses
        self._tracks = []
        self._next_id = 1  # never reused, so a dropped id cannot be confused with a new person

    @property
    def tracks(self):
        return self._tracks

    def update(self, boxes, scores):
        boxes = np.asarray(boxes, dtype=np.float32).reshape(-1, 4)
        scores = np.asarray(scores, dtype=np.float32).reshape(-1)

        pairs = []
        for ti, tr in enumerate(self._tracks):
            pred = tr.box + tr.vel
            for di in range(len(boxes)):
                v = iou(pred, boxes[di])
                if v >= self.iou_min:
                    pairs.append((v, ti, di))
        pairs.sort(key=lambda p: -p[0])

        used_t, used_d = set(), set()
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            tr = self._tracks[ti]
            new = boxes[di].copy()
            tr.vel = 0.5 * tr.vel + 0.5 * (new - tr.box)
            tr.box = new
            tr.score = float(scores[di])
            tr.hits += 1
            tr.misses = 0

        for ti, tr in enumerate(self._tracks):
            if ti not in used_t:
                tr.misses += 1
        self._tracks = [t for t in self._tracks if t.misses <= self.max_misses]

        for di in range(len(boxes)):
            if di not in used_d:
                self._tracks.append(Track(
                    id=self._next_id, box=boxes[di].copy(), score=float(scores[di]),
                    min_hits=self.min_hits))
                self._next_id += 1
        return list(self._tracks)
