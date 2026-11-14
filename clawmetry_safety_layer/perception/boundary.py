"""L2 boundary perception: "is the robot about to leave the go zone?" from the camera, without floor tape.

No tape may be stuck on the Leukerbad Therme floor (venue rule, docs/D1W-RUNBOOK.md "Venue permission"), so the
default detector reads the floors the venue already has. Three detectors, chosen by perception.boundary.detector:

  floor_transition (default)
      The concourse's floor materials differ by area. Per-class HSV colour histograms (concourse_tile / wood /
      terracotta / checker_tile / wet_deck) are calibrated from frames of assets/venue/spa_walkthrough.mp4 and
      docs/venue/*.jpg (scripts/calibrate_floor.py -> perception/floor_classes.json). The lower part of the frame is
      cut into horizontal strips; each strip is classified; walking up from the bottom, the first stable change of
      class is the transition, and its image row gives "transition ahead at ~N m" through the camera geometry.
      `no_go` classes (default: wet_deck) beyond the transition raise no_go_ahead.
  colour_marker
      Cones (or tape, if the venue ever allows it): an HSV band from config; the nearest marker blob's bottom row
      gives the distance.
  apriltag_board
      Printed 36h11 tags on easels at known spots: a seen tag is a position fix (landmark name, range, bearing) for
      refix_at_landmark(); it says nothing about the floor.

Everything here is numpy + OpenCV on the CPU, a few ms per frame at 640 px. No frame is stored.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .camera import Camera
from .observation import Boundary

ROOT = Path(__file__).resolve().parent.parent
FLOOR_CLASSES = ("concourse_tile", "wood", "terracotta", "checker_tile", "wet_deck")
H_BINS, S_BINS, V_BINS = 18, 8, 4


def _cv2():
    import cv2

    return cv2


def hsv_hist(bgr: np.ndarray) -> np.ndarray:
    """Normalised H x S x V histogram of a BGR patch (sums to 1)."""
    cv2 = _cv2()
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1, 2], None, [H_BINS, S_BINS, V_BINS], [0, 180, 0, 256, 0, 256]).ravel()
    s = float(h.sum())
    return (h / s).astype(np.float32) if s > 0 else h.astype(np.float32)


def bhattacharyya(a: np.ndarray, b: np.ndarray) -> float:
    """0 = identical distributions, 1 = disjoint."""
    bc = float(np.sum(np.sqrt(np.maximum(a, 0) * np.maximum(b, 0))))
    return math.sqrt(max(0.0, 1.0 - bc))


@dataclass
class FloorModel:
    """Nearest-exemplar classifier over HSV histograms. Each labelled patch is cut into 4 horizontal sub-patches
    (strip-sized) and every one is kept as an exemplar: on the 2026-11-14 walkthrough labels this scored 0.77
    leave-one-frame-out accuracy vs 0.62 for per-class mean histograms (docs/D1W-RUNBOOK.md)."""

    classes: dict[str, list[np.ndarray]] = field(default_factory=dict)  # class -> exemplar histograms
    no_go: tuple[str, ...] = ("wet_deck",)
    max_distance: float = 0.60  # a strip farther than this from every exemplar is "unknown"
    meta: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> FloorModel:
        d = json.loads(Path(path).read_text())
        classes: dict[str, list[np.ndarray]] = {}
        for k, v in d["classes"].items():
            rows = v if v and isinstance(v[0], list) else [v]  # older files: one mean histogram per class
            classes[k] = [np.asarray(r, dtype=np.float32) for r in rows]
        return cls(classes, tuple(d.get("no_go", ["wet_deck"])), float(d.get("max_distance", 0.60)), d.get("meta", {}))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(
                {
                    "bins": [H_BINS, S_BINS, V_BINS],
                    "classes": {k: [[round(float(x), 5) for x in h] for h in v] for k, v in self.classes.items()},
                    "no_go": list(self.no_go),
                    "max_distance": self.max_distance,
                    "meta": self.meta,
                },
                separators=(",", ":"),
            )
        )

    @classmethod
    def fit(
        cls, samples: dict[str, list[np.ndarray]], no_go=("wet_deck",), meta: dict | None = None, split: int = 4
    ) -> FloorModel:
        """samples: class -> list of BGR patches. Unknown class names are refused (keeps the label set fixed)."""
        bad = [k for k in samples if k not in FLOOR_CLASSES]
        if bad:
            raise ValueError(f"unknown floor classes {bad}; use {FLOOR_CLASSES}")
        classes: dict[str, list[np.ndarray]] = {}
        for k, patches in samples.items():
            for p in patches:
                if p is None or not p.size:
                    continue
                n = split if p.shape[0] >= 4 * split else 1
                for i in range(n):
                    sub = p[i * p.shape[0] // n : (i + 1) * p.shape[0] // n]
                    classes.setdefault(k, []).append(hsv_hist(sub))
        return cls(classes, tuple(no_go), meta=meta or {})

    def classify(self, patch: np.ndarray) -> tuple[str, float]:
        h = hsv_hist(patch)
        best, bd = "unknown", 1.0
        for k, refs in self.classes.items():
            for ref in refs:
                d = bhattacharyya(h, ref)
                if d < bd:
                    best, bd = k, d
        if bd > self.max_distance:
            return "unknown", bd
        return best, bd


class FloorTransitionDetector:
    name = "floor_transition"

    def __init__(self, model: FloorModel | None, camera: Camera, band: float = 0.40, strips: int = 12, stable: int = 2):
        self.model = model
        self.cam = camera
        self.band = band  # lower fraction of the frame that sees floor
        self.strips = strips
        self.stable = stable  # strips in a row of the new class before it counts

    def strip_classes(self, frame: np.ndarray) -> list[tuple[int, str, float]]:
        """(centre row, class, distance) for each strip, bottom first."""
        h, w = frame.shape[:2]
        top = int(h * (1 - self.band))
        sh = max(2, (h - top) // self.strips)
        x0, x1 = int(w * 0.25), int(w * 0.75)  # the path ahead, not the walls
        out = []
        for i in range(self.strips):
            y1 = h - i * sh
            y0 = max(top, y1 - sh)
            if y1 - y0 < 2:
                break
            cls, d = self.model.classify(frame[y0:y1, x0:x1]) if self.model else ("unknown", 1.0)
            out.append(((y0 + y1) // 2, cls, d))
        return out

    def detect(self, frame: np.ndarray) -> Boundary:
        if self.model is None or not self.model.classes:
            return Boundary(self.name, confidence=0.0)
        h = frame.shape[0]
        rows = self.strip_classes(frame)
        if not rows:
            return Boundary(self.name)
        here = rows[0][1]
        run_cls, run_n, run_row = None, 0, None
        for row, cls, _ in rows[1:]:
            if cls in (here, "unknown"):
                run_cls, run_n, run_row = None, 0, None
                continue
            if cls == run_cls:
                run_n += 1
            else:
                run_cls, run_n, run_row = cls, 1, row
            if run_n >= self.stable and run_row is not None:
                ahead = self.cam.row_to_distance(run_row, h)
                conf = 1.0 - float(np.mean([d for _, c, d in rows if c == run_cls]))
                return Boundary(
                    self.name,
                    ahead_m=ahead,
                    floor_here=here,
                    floor_ahead=run_cls,
                    no_go_ahead=run_cls in self.model.no_go,
                    confidence=round(conf, 3),
                )
        conf = 1.0 - float(np.mean([d for _, c, d in rows if c == here]))
        return Boundary(self.name, floor_here=here, no_go_ahead=here in self.model.no_go, confidence=round(conf, 3))


class ColourMarkerDetector:
    """Cones / tape of one colour (HSV band). The nearest blob's bottom row -> distance."""

    name = "colour_marker"

    def __init__(self, camera: Camera, hsv_lo=(5, 150, 120), hsv_hi=(20, 255, 255), min_area_frac=0.0008, no_go=True):
        self.cam = camera
        self.lo, self.hi = np.array(hsv_lo, np.uint8), np.array(hsv_hi, np.uint8)
        self.min_area_frac = min_area_frac
        self.no_go = no_go  # markers delimit the go zone: seeing one ahead means the edge is ahead

    def detect(self, frame: np.ndarray) -> Boundary:
        cv2 = _cv2()
        h, w = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.lo, self.hi)
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        best = None
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            if area < self.min_area_frac * h * w:
                continue
            bottom = y + bh
            if best is None or bottom > best:
                best = bottom
        if best is None:
            return Boundary(self.name)
        return Boundary(self.name, ahead_m=self.cam.row_to_distance(best, h), no_go_ahead=self.no_go, confidence=0.8)


class AprilTagBoardDetector:
    """Printed 36h11 tags on easels: tag id -> landmark name (zones file `landmarks`). Gives a fix, not a floor."""

    name = "apriltag_board"

    def __init__(self, camera: Camera, boards: dict[int, str], tag_size_m: float = 0.16):
        self.cam = camera
        self.boards = {int(k): v for k, v in boards.items()}
        self.tag_size_m = tag_size_m
        self._det: Any = None

    def detect(self, frame: np.ndarray) -> Boundary:
        cv2 = _cv2()
        if self._det is None:
            d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
            self._det = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters())
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = self._det.detectMarkers(gray)
        if ids is None:
            return Boundary(self.name)
        h, w = frame.shape[:2]
        best: dict[str, Any] | None = None
        for c, i in zip(corners, ids.ravel(), strict=False):
            if int(i) not in self.boards:
                continue
            pts = c.reshape(4, 2)
            side = float(np.mean([np.linalg.norm(pts[k] - pts[(k + 1) % 4]) for k in range(4)]))
            rng = self.cam.focal_px(h) * self.tag_size_m / max(side, 1.0)
            fix = {
                "landmark": self.boards[int(i)],
                "tag_id": int(i),
                "range_m": round(rng, 2),
                "bearing_rad": round(self.cam.bearing(float(pts[:, 0].mean()), w, h), 3),
            }
            if best is None or rng < float(best["range_m"]):
                best = fix
        if best is None:
            return Boundary(self.name)
        return Boundary(self.name, fix=best, confidence=0.9)


def make_boundary(cfg: dict):
    pc = cfg.get("perception") or {}
    bc = pc.get("boundary") or {}
    cam = Camera.from_cfg(pc.get("camera"))
    kind = bc.get("detector", "floor_transition")
    if kind == "floor_transition":
        p = Path(bc.get("model", "perception/floor_classes.json"))
        p = p if p.is_absolute() else ROOT / p
        model = FloorModel.load(p) if p.exists() else None
        return FloorTransitionDetector(model, cam, float(bc.get("band", 0.40)), int(bc.get("strips", 12)))
    if kind == "colour_marker":
        return ColourMarkerDetector(
            cam, tuple(bc.get("hsv_lo", (5, 150, 120))), tuple(bc.get("hsv_hi", (20, 255, 255)))
        )
    if kind == "apriltag_board":
        return AprilTagBoardDetector(cam, bc.get("boards") or {}, float(bc.get("tag_size_m", 0.16)))
    raise ValueError(
        f"perception.boundary.detector must be floor_transition | colour_marker | apriltag_board, not {kind!r}"
    )
