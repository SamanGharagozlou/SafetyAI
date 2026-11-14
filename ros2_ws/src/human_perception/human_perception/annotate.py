"""Draw person boxes for the optional annotated debug image (no identities)."""

from typing import Sequence

import numpy as np

from .detection import PersonDetection

_COLOR = (0, 200, 0)  # BGR


def draw_detections(frame: np.ndarray, detections: Sequence[PersonDetection]) -> np.ndarray:
    """Return a copy of ``frame`` with boxes and ``person 0.87`` labels."""
    import cv2

    out = frame.copy()
    thickness = max(1, int(round(min(out.shape[:2]) / 320)))
    for det in detections:
        p1 = (int(det.x1), int(det.y1))
        p2 = (int(det.x2), int(det.y2))
        cv2.rectangle(out, p1, p2, _COLOR, thickness)
        label = f'person {det.score:.2f}'
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        top = max(0, p1[1] - th - base - 2)
        cv2.rectangle(out, (p1[0], top), (p1[0] + tw + 2, top + th + base + 2), _COLOR, -1)
        cv2.putText(out, label, (p1[0] + 1, top + th + 1), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 0, 0), 1, cv2.LINE_AA)
    cv2.putText(out, f'people: {len(detections)}', (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (0, 0, 255), 2, cv2.LINE_AA)
    return out
