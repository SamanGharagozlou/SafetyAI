"""RobotBridge: the contract every body implements.

Everything above this file (skills, agent, ClawMetry) is body-agnostic.
"""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

# G1 tray-handling arm poses (radians), 14 arm joints, left then right:
# [shoulder_pitch, shoulder_roll, shoulder_yaw, elbow, wrist_roll, wrist_pitch, wrist_yaw] x 2
# Solved 2026-11-11 by numerical IK (scripts/solve_arm_poses.py) against scenes/g1_hotel.xml with the
# unitree_mujoco G1 29-DoF model (pelvis 0.793 m). Grasp site = midpoint of the two palm centres; every pose
# keeps the same left-wrist orientation (<=4 deg apart), so a prop welded to the left wrist stays level.
# These are sent to the REAL G1 arms too (rt/arm_sdk): check them on the harness first.  # VERIFY
#   HOLD         grasp site 1.068 m high, 0.427 m in front of the pelvis (tray base ~1.0 m)
#   REACH_SHELF  0.936 m (shelf / desk top 0.85 + 0.08), 0.344 m forward, hands 4 cm open per side
#   CLOSE_SHELF  0.936 m, 0.353 m forward, palms squeezing a 0.35 m wide prop
#   REACH_TABLE  0.904 m (table B top 0.80 + 0.08 target, 2.4 cm short), 0.332 m forward, open
#   CLOSE_TABLE  0.904 m, 0.341 m forward, squeezing
#   LIFT_SHELF   1.025 m, 0.364 m forward: straight up ~9 cm from CLOSE_SHELF (clears the shelf before carrying)
#   LIFT_TABLE   0.979 m, 0.358 m forward: ~7.5 cm above CLOSE_TABLE (descend vertically onto the table)
# The grasp site sits 8 cm (not 3 cm) above the surface because the G1 palms are ~0.15 m tall: at 3 cm their
# lowest point was 4 cm inside the shelf. Previous hand-tuned HOLD (-1.0, +-0.25, 0, 1.0) / LOWER below.
ARM_TRAY_HOLD = [
    -0.830,
    +0.372,
    +0.066,
    +0.718,
    -0.015,
    -0.348,
    +0.020,
    -0.830,
    -0.372,
    -0.066,
    +0.718,
    +0.015,
    -0.348,
    -0.020,
]
ARM_REACH_SHELF = [
    -0.417,
    +0.354,
    +0.073,
    +0.923,
    -0.039,
    -0.946,
    +0.024,
    -0.417,
    -0.354,
    -0.073,
    +0.923,
    +0.039,
    -0.946,
    -0.024,
]
ARM_CLOSE_SHELF = [
    -0.413,
    +0.276,
    +0.027,
    +0.813,
    +0.071,
    -0.836,
    +0.142,
    -0.413,
    -0.276,
    -0.027,
    +0.813,
    -0.071,
    -0.836,
    -0.142,
]
ARM_REACH_TABLE = [
    -0.452,
    +0.321,
    +0.062,
    +1.204,
    +0.034,
    -1.157,
    +0.096,
    -0.452,
    -0.321,
    -0.062,
    +1.204,
    -0.034,
    -1.157,
    -0.096,
]
ARM_CLOSE_TABLE = [
    -0.419,
    +0.255,
    +0.003,
    +1.031,
    +0.121,
    -1.004,
    +0.214,
    -0.419,
    -0.255,
    -0.003,
    +1.031,
    -0.121,
    -1.004,
    -0.214,
]
ARM_LIFT_SHELF = [
    -0.416,
    +0.345,
    +0.072,
    +0.251,
    -0.020,
    -0.316,
    +0.044,
    -0.416,
    -0.345,
    -0.072,
    +0.251,
    +0.020,
    -0.316,
    -0.044,
]
ARM_LIFT_TABLE = [
    -0.382,
    +0.309,
    +0.043,
    +0.486,
    +0.018,
    -0.570,
    +0.087,
    -0.382,
    -0.309,
    -0.043,
    +0.486,
    -0.018,
    -0.570,
    -0.087,
]
# grasp-site geometry of the poses above, in the pelvis frame (forward m, height m)
GRASP_FWD = {"SHELF": 0.353, "TABLE": 0.341}  # docking stand-off: pelvis -> grasp point
GRASP_Z = {"SHELF": 0.936, "TABLE": 0.904, "HOLD": 1.068}
ARM_REACH = {"SHELF": ARM_REACH_SHELF, "TABLE": ARM_REACH_TABLE}
ARM_CLOSE = {"SHELF": ARM_CLOSE_SHELF, "TABLE": ARM_CLOSE_TABLE}
ARM_LIFT = {"SHELF": ARM_LIFT_SHELF, "TABLE": ARM_LIFT_TABLE}
# which pose family serves which surface (desk A is built at shelf height)
SURFACE_POSE = {"shelf_corridor": "SHELF", "table_A": "SHELF", "table_B": "TABLE"}
# legacy carry-lower pose (no longer used by pick/place; kept for probe/manual use)
ARM_TRAY_LOWER = [
    -0.60,
    0.25,
    0.00,
    1.20,
    0.00,
    0.00,
    0.00,
    -0.60,
    -0.25,
    0.00,
    1.20,
    0.00,
    0.00,
    0.00,
]
ARM_REST = [0.0] * 14

# bodies that are real hardware (never time-compressed, "hardware" environment on the Physical AI page)
HARDWARE_BODIES = ("g1", "go2", "panther", "agibot_x2", "agibot_a2", "agibot_d1w")  # d1w mock: "agibot_d1w_mock"

NOT_STANDING_MSG = "robot is not standing; stand it with the remote first"
LOWSTATE_MAX_AGE_S = 1.0  # low-state older than this is not evidence the robot is upright now


def cfg_flag(cfg: dict, key: str) -> bool:
    """Safety flags are enabled only by a literal YAML `true` (a string like "false" stays off)."""
    return cfg.get(key, False) is True


def require_upright(last_low, body: str, max_age_s: float = LOWSTATE_MAX_AGE_S) -> None:
    """Raise unless the latest low-state shows an upright robot with live joint readings.

    last_low is (t_received, tilt_ok, legs_nonzero) or None. Used by real-robot bridges when
    auto_stand is false: they must not command any stand/damp transition themselves.
    """
    why = None
    if last_low is None:
        why = "no low-state received yet"
    else:
        t, tilt_ok, legs_nonzero = last_low
        age = time.time() - t
        if age > max_age_s:
            why = f"latest low-state is {age:.1f}s old"
        elif not tilt_ok:
            why = "IMU roll/pitch out of range"
        elif not legs_nonzero:
            why = "all leg joint readings are zero"
    if why:
        print(f"[{body}] stand() refused: {why}")
        raise RuntimeError(NOT_STANDING_MSG)


@dataclass
class RobotState:
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0
    zone: str = "unknown"
    battery_pct: float = 100.0
    mode: str = "idle"  # idle | standing | walking | damped | paused
    balance_ok: bool = True
    holding: str | None = None  # "tray" when the arms are in a hold pose
    arm_q: list[float] = field(default_factory=lambda: list(ARM_REST))
    body: str = "fake"
    ts: float = field(default_factory=time.time)

    def dict(self) -> dict:
        d = asdict(self)
        d["arm_q"] = [round(v, 3) for v in d["arm_q"]]
        for k in ("x", "y", "yaw", "battery_pct"):
            d[k] = round(d[k], 3)
        return d

    def dist_to(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)


class RobotBridge(ABC):
    body = "abstract"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.speed_scale = float(cfg.get("speed_scale", 1.0))
        self.settle_timeout_s = float(cfg.get("settle_timeout_s", 3.0))
        # event sink: Skills sets this to Skills.emit so a bridge can put its own events (refused commands,
        # transport faults) into the robot-session trace. Signature: sink(kind, **data). None = print only.
        self.event_sink: Any = None

    def _event(self, kind: str, **data) -> None:
        print(f"[{self.body}] {kind} {data}")
        if self.event_sink is not None:
            try:
                self.event_sink(kind, **data)
            except Exception as e:  # noqa: BLE001  (a trace sink must never break the body)
                print(f"[{self.body}] event sink failed on {kind}: {e}")

    # ---- lifecycle -------------------------------------------------------
    @abstractmethod
    def connect(self, iface: str, domain: int) -> None: ...

    def close(self) -> None:  # noqa: B027  optional hook: bodies with resources override it
        pass

    # ---- sensing ---------------------------------------------------------
    @abstractmethod
    def state(self) -> RobotState: ...

    # ---- locomotion (through the vendor's balance controller) -------------
    @abstractmethod
    def stand(self) -> None: ...

    @abstractmethod
    def move_velocity(self, vx: float, vy: float, vyaw: float) -> None:
        """Set a body-frame velocity. Caller is responsible for timing."""

    @abstractmethod
    def stop(self) -> None:
        """Zero velocity immediately (no settle wait)."""

    def stop_and_settle(self) -> RobotState:
        """Zero velocity and wait until the body reports balance_ok."""
        self.stop()
        t0 = time.time()
        while time.time() - t0 < self.settle_timeout_s:
            s = self.state()
            if s.balance_ok and s.mode != "walking":
                return s
            time.sleep(0.05)
        return self.state()

    @abstractmethod
    def damp(self) -> None:
        """Soft stop: vendor 'damp' mode. Never called by hold_pose; real bridges refuse it unless
        the config sets allow_damp: true."""

    # ---- arms ------------------------------------------------------------
    @abstractmethod
    def set_arm_targets(self, q: list[float], ramp_s: float = 2.0) -> None:
        """Ramp the 14 arm joints to q over ramp_s seconds (blocking)."""

    # ---- props (optional; only bodies that can sense them implement these) ------------------
    # Default: no geometry / physical confirmation -> None, and the skills run the same arm
    # choreography with timing only (that is the path the real G1 takes today: no perception).
    def target_point(self, target: str, obj: str | None = None):
        """World (x, y, z) of a prop's grasp point or of a place target's slot for obj; None if unknown."""
        return None

    def prop_support(self, obj: str) -> str | None:
        """What the prop rests on (a place target name, 'floor (zone X)', ...); None if unknown."""
        return None

    def grasp_distance(self, obj: str) -> float | None:
        """Distance hands grasp site -> prop grasp point (m); None if unknown."""
        return None

    def approach_normal(self, tx: float, ty: float, rx: float, ry: float):
        """Outward unit normal (nx, ny) of the furniture edge to dock at for a target at (tx, ty), seen from a
        robot at (rx, ry); None if the target is not on furniture / unknown."""
        return None

    def footprint_blocked(self, x: float, y: float, margin: float | None = None) -> str | None:
        """Name of the furniture whose footprint (plus margin; default = the body's docking clearance)
        contains base point (x, y), else None."""
        return None

    def attach(self, obj: str) -> bool | None:
        """Weld the prop to the hands where it is (no repositioning). False = hands not at the prop;
        None = not supported by this body."""
        return None

    def release(self, obj: str, target: str | None = None) -> str | None:
        """Let go of the object; return what it came to rest on, or None if this body cannot tell."""
        return None

    # ---- speech / gaze (optional) ------------------------------------------
    def say(self, text: str) -> None:
        print(f"[robot says] {text}")

    def look_at(self, target: str) -> None:  # noqa: B027  optional hook: bodies with a head/camera override it
        pass

    # ---- helpers for subclasses ---------------------------------------------
    def _ramp(self, q_from: list[float], q_to: list[float], ramp_s: float, apply):
        steps = max(1, int(ramp_s / 0.02))
        for i in range(1, steps + 1):
            a = i / steps
            apply([f + (t - f) * a for f, t in zip(q_from, q_to, strict=False)])
            time.sleep(0.02)
