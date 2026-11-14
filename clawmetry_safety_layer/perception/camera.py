"""Pinhole geometry for the robot's camera: image row -> floor distance, box height -> person distance.

Camera config (perception.camera in config/d1w.yaml, all # VERIFY on site with a tape measure):
  height_m   lens height above the floor
  pitch_deg  optical axis vs horizontal; NEGATIVE = tilted up (the D1 camera is low and looks up)
  vfov_deg   vertical field of view
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Camera:
    height_m: float = 0.30
    pitch_deg: float = -10.0
    vfov_deg: float = 60.0

    @classmethod
    def from_cfg(cls, c: dict | None) -> Camera:
        c = c or {}
        return cls(float(c.get("height_m", 0.30)), float(c.get("pitch_deg", -10.0)), float(c.get("vfov_deg", 60.0)))

    def focal_px(self, img_h: int) -> float:
        return (img_h / 2) / math.tan(math.radians(self.vfov_deg) / 2)

    def row_to_distance(self, row: float, img_h: int) -> float | None:
        """Floor distance (m, along the ground) to the point seen at image row `row` (0 = top). None if that row
        looks at or above the horizon."""
        f = self.focal_px(img_h)
        # angle below the horizon = (pixel angle below the optical axis) + (axis angle below the horizon = pitch_deg)
        a = math.atan((row - img_h / 2) / f) + math.radians(self.pitch_deg)
        if a <= math.radians(0.5):
            return None
        return self.height_m / math.tan(a)

    def lowest_floor_row_distance(self, img_h: int) -> float | None:
        return self.row_to_distance(img_h - 1, img_h)

    def person_distance(self, box_h_px: float, img_h: int, person_h_m: float) -> float | None:
        """Distance from a full-body box height (pinhole). Boxes cut off by the frame edge underestimate the height:
        the caller passes only uncut boxes, or accepts an upper bound."""
        if box_h_px <= 2:
            return None
        return self.focal_px(img_h) * person_h_m / box_h_px

    def bearing(self, u: float, img_w: int, img_h: int) -> float:
        """Horizontal angle of image column u (+ = left), square pixels assumed."""
        f = self.focal_px(img_h)
        return -math.atan((u - img_w / 2) / f)
