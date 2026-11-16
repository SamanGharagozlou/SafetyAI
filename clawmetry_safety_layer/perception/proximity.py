"""Person distance from detection boxes, plus a child heuristic. # VERIFY on site with people at 1 m / 2 m / 3 m.

The D1 camera is low (~0.3 m) and tilted up, so near people are often cut off at the top (head out of frame) or,
very near, at both edges. Rules:
  * feet visible (box bottom inside the frame, below the horizon): distance from the foot row through the camera
    geometry (height, pitch) -- independent of how tall the person is.
  * feet cut off at the bottom edge: the feet stand closer than the nearest floor the camera sees (the bottom row's
    floor distance, ~0.8 m for 0.3 m / -10 deg), so the distance is min(pinhole estimate, that floor distance). A
    pinhole estimate alone would be unsafe: a legs-only box is short, so it would look far away.
  * cut at both edges: "very near" -> 0.8 m (below the 1 m stop).
  * child: feet visible, head visible, and implied height (foot distance * box_h / f) under 1.3 m.
"""

from __future__ import annotations

from .camera import Camera
from .observation import Person

EDGE_PX = 3


def persons_from(detections: list[dict], w: int, h: int, cam: Camera, person_h_m: float = 1.70, min_conf=0.35):
    out: list[Person] = []
    if not w or not h:
        return out
    f = cam.focal_px(h)
    for d in detections:
        if d.get("class") != "person" or float(d.get("conf", 0)) < min_conf:
            continue
        x1, y1, x2, y2 = (float(v) for v in d["box"])
        bh = max(1.0, y2 - y1)
        cut_top, cut_bot = y1 <= EDGE_PX, y2 >= h - EDGE_PX
        dfoot = None if cut_bot else cam.row_to_distance(y2, h)
        if cut_top and cut_bot:
            dist: float | None = 0.8
        elif dfoot is not None:
            dist = dfoot
        else:
            dist = cam.person_distance(bh, h, person_h_m)
            if cut_bot:
                floor_min = cam.lowest_floor_row_distance(h)
                if floor_min is not None:
                    dist = floor_min if dist is None else min(dist, floor_min)
        child = bool(dfoot is not None and not cut_top and dfoot * bh / f < 1.3)
        out.append(
            Person(
                distance_m=None if dist is None else round(dist, 2),
                conf=float(d.get("conf", 0)),
                child=child,
                bearing_rad=round(cam.bearing((x1 + x2) / 2, w, h), 3),
                box=[x1, y1, x2, y2],
            )
        )
    return out


def summarise(persons: list[Person]) -> tuple[float | None, int, bool]:
    """(nearest distance, people within 2 m, a child within 2 m)."""
    ds = [p.distance_m for p in persons if p.distance_m is not None]
    nearest = min(ds) if ds else None
    within = sum(1 for d in ds if d < 2.0)
    child = any(p.child and p.distance_m is not None and p.distance_m < 2.0 for p in persons)
    return nearest, within, child
