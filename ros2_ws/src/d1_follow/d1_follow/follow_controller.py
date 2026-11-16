from dataclasses import dataclass


@dataclass
class FollowParams:
    target_height_frac: float = 0.6
    k_yaw: float = 1.0
    k_x: float = 1.2
    yaw_deadband: float = 0.08
    x_deadband: float = 0.05
    max_vx: float = 0.5
    max_wz: float = 0.8


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class FollowController:
    """P controller: box centre -> yaw rate, box height -> forward speed."""

    def __init__(self, params=None):
        self.p = params or FollowParams()

    def compute(self, box_xyxy, frame_w, frame_h):
        if box_xyxy is None:
            return 0.0, 0.0
        p = self.p
        x1, y1, x2, y2 = (float(v) for v in box_xyxy)
        ex = ((x1 + x2) / 2.0 - frame_w / 2.0) / (frame_w / 2.0)
        # ROS: positive wz turns left, so a person on the right (ex > 0) gives wz < 0
        wz = 0.0
        if abs(ex) >= p.yaw_deadband:
            wz = _clamp(-p.k_yaw * ex, -p.max_wz, p.max_wz)
        e = p.target_height_frac - (y2 - y1) / frame_h
        vx = 0.0
        if abs(e) >= p.x_deadband:
            # never reverse: a too-close person just stops the robot
            vx = _clamp(p.k_x * e, 0.0, p.max_vx)
        return float(vx), float(wz)
