"""The one record perception hands to the safety monitor / FSM and to the robot-session trace (turn.observe).

No pixels in here: detections, distances, classes and flags only (docs/PRIVACY.md). Frames live only inside the
pipeline loop and the sampler's in-flight request.
"""

from __future__ import annotations

import builtins
import time
from dataclasses import asdict, dataclass, field


@dataclass
class Person:
    distance_m: float | None  # None = could not estimate (box cut off, camera geometry unknown)
    conf: float
    child: bool = False
    bearing_rad: float = 0.0  # + = left of the camera axis
    box: list[float] = field(default_factory=list)  # x1, y1, x2, y2 in source pixels


@dataclass
class Boundary:
    detector: str  # floor_transition | colour_marker | apriltag_board
    ahead_m: float | None = None  # distance to the nearest transition / marker ahead, None = none seen
    floor_here: str | None = None  # floor class under the robot (floor_transition)
    floor_ahead: str | None = None  # floor class beyond the transition
    no_go_ahead: bool = False  # the floor ahead / the marker belongs to a no-go area
    fix: dict | None = None  # apriltag_board: {"landmark", "tag_id", "range_m", "bearing_rad"}
    confidence: float = 0.0


@dataclass
class Observation:
    ts: float = field(default_factory=time.time)
    frame_seq: int = -1
    source: str = "none"
    width: int = 0
    height: int = 0
    detections: list[dict] = field(default_factory=list)  # [{"class", "conf", "box"}], source pixels
    detector: str = "none"  # team | local_yolo | none
    detector_age_s: float | None = None
    persons: list[Person] = field(default_factory=list)
    nearest_person_m: float | None = None
    people_within_2m: int = 0
    child_near: bool = False
    boundary: Boundary | None = None
    scene: str = "unknown"
    scene_conf: float = 0.0
    camera_off: bool = False  # in a camera_off zone: nothing was looked at
    degraded: list[str] = field(default_factory=list)  # e.g. ["detector_stale", "scene_unavailable"]

    def dict(self) -> dict:
        d = asdict(self)
        for k in ("nearest_person_m", "detector_age_s"):
            if d[k] is not None:
                d[k] = round(d[k], 2)
        d["ts"] = round(d["ts"], 3)
        return d

    def summary(self) -> builtins.dict:
        """Small version for the trace (turn.observe) and the console."""
        b = self.boundary
        return {
            "scene": self.scene,
            "people_within_2m": self.people_within_2m,
            "nearest_person_m": None if self.nearest_person_m is None else round(self.nearest_person_m, 2),
            "child_near": self.child_near,
            "boundary_ahead_m": None if b is None or b.ahead_m is None else round(b.ahead_m, 2),
            "no_go_ahead": bool(b and b.no_go_ahead),
            "detector": self.detector,
            "camera_off": self.camera_off,
            "degraded": list(self.degraded),
        }
