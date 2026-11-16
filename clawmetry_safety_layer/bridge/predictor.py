"""World-model interface for predictive breach detection.

A Predictor rolls a command forward from the current RobotState over a horizon and returns a Prediction
(trajectory samples, zone sequence, speed, hold height, when the command ends). The skills' command gate runs it
before every actuator command and checks the result against policies/behaviour.yaml; a predicted breach blocks the
command before anything moves. Predictor v1 (KinematicPredictor) is the bridges' own kinematic model: the
FakeBridge integrator (body-frame velocity, yaw first, 50 Hz) with the bridge's speed_scale. No learning. A learned
world model replaces it by implementing Predictor.predict / predict_goto with the same return type.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .base import RobotState

SUBSTEP_S = 0.02  # FakeBridge integrates at 50 Hz


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


@dataclass
class VelocityCommand:
    """Body-frame velocity, held until it has moved until_m metres / turned until_yaw radians (the skill's own
    termination condition), or for the whole horizon when neither is given."""

    vx: float
    vy: float
    vyaw: float
    until_m: float | None = None
    until_yaw: float | None = None
    kind: str = "velocity"


@dataclass
class ArmCommand:
    q: list[float]
    ramp_s: float
    hold_height_m: float | None = None  # grasp-site height at the end of the ramp (None = unknown / not holding)
    kind: str = "arm"


@dataclass
class GotoPlan:
    """navigate_to's closed-loop plan: walk to goal with the skill's steering law under per-zone caps."""

    goal: tuple[float, float]
    tol_m: float
    max_s: float
    kind: str = "goto"


@dataclass
class Prediction:
    model: str
    horizon_s: float
    t: list[float] = field(default_factory=list)
    x: list[float] = field(default_factory=list)
    y: list[float] = field(default_factory=list)
    yaw: list[float] = field(default_factory=list)
    v: list[float] = field(default_factory=list)  # linear speed at each sample (m/s, physical)
    zones: list[str] = field(default_factory=list)  # zone sequence (consecutive duplicates removed)
    speed_mps: float = 0.0  # max linear speed over the horizon
    yaw_rate: float = 0.0
    hold_height_m: float | None = None
    duration_s: float | None = None  # predicted time until the command / plan completes
    end: tuple[float, float, float] | None = None  # predicted pose when it completes
    path_m: float | None = None  # predicted path length to completion

    def samples(self):
        return zip(self.t, self.x, self.y, self.yaw, strict=False)

    def event(self) -> dict:
        r = lambda v: [round(a, 3) for a in v]  # noqa: E731
        return {
            "model": self.model,
            "horizon_s": self.horizon_s,
            "t": r(self.t),
            "x": r(self.x),
            "y": r(self.y),
            "yaw": r(self.yaw),
            "v": r(self.v),
            "zones": self.zones,
            "speed_mps": round(self.speed_mps, 3),
            "yaw_rate": round(self.yaw_rate, 3),
            "hold_height_m": self.hold_height_m,
            "duration_s": None if self.duration_s is None else round(self.duration_s, 3),
            "end": None if self.end is None else [round(v, 3) for v in self.end],
            "path_m": None if self.path_m is None else round(self.path_m, 3),
        }


class Predictor(ABC):
    name = "abstract"

    @abstractmethod
    def predict(self, s: RobotState, cmd, horizon_s: float, dt: float, zone_of) -> Prediction: ...

    @abstractmethod
    def predict_goto(
        self, s: RobotState, plan: GotoPlan, horizon_s: float, dt: float, zone_of, cap_of, settle_s: float = 0.6
    ) -> Prediction: ...


class KinematicPredictor(Predictor):
    name = "kinematic-v1"

    def __init__(self, speed_scale: float = 1.0):
        self.speed_scale = float(speed_scale)

    def _step(self, x, y, yaw, vx, vy, vyaw, dt):
        yaw = _wrap(yaw + vyaw * dt)
        x += (vx * math.cos(yaw) - vy * math.sin(yaw)) * dt
        y += (vx * math.sin(yaw) + vy * math.cos(yaw)) * dt
        return x, y, yaw

    def predict(self, s, cmd, horizon_s, dt, zone_of) -> Prediction:
        p = Prediction(self.name, horizon_s)
        x, y, yaw = s.x, s.y, s.yaw
        if isinstance(cmd, ArmCommand):
            p.hold_height_m = cmd.hold_height_m
            p.duration_s = float(cmd.ramp_s)
            p.end = (x, y, yaw)
            p.path_m = 0.0
            n = max(1, int(round(horizon_s / dt)))
            for i in range(n + 1):
                p.t.append(i * dt)
                p.x.append(x)
                p.y.append(y)
                p.yaw.append(yaw)
                p.v.append(0.0)
            p.zones = [zone_of(x, y)]
            return p
        vx, vy, vyaw = cmd.vx * self.speed_scale, cmd.vy * self.speed_scale, cmd.vyaw
        lin = math.hypot(vx, vy)
        # analytic end of the command (may lie beyond the horizon)
        ends = []
        if cmd.until_m is not None and lin > 1e-9:
            ends.append(abs(cmd.until_m) / lin)
        if cmd.until_yaw is not None and abs(vyaw) > 1e-9:
            ends.append(abs(cmd.until_yaw) / abs(vyaw))
        t_end = min(ends) if ends else None
        p.duration_s = t_end
        p.speed_mps, p.yaw_rate = (lin, abs(vyaw)) if (t_end is None or t_end > 0) else (0.0, 0.0)
        t, travelled, next_sample = 0.0, 0.0, 0.0
        stop_at = horizon_s if t_end is None else min(horizon_s, t_end)
        while t <= horizon_s + 1e-9:
            if t >= next_sample - 1e-9:
                p.t.append(round(t, 4))
                p.x.append(x)
                p.y.append(y)
                p.yaw.append(yaw)
                p.v.append(lin if t < stop_at - 1e-9 else 0.0)
                next_sample += dt
            if t < stop_at - 1e-9:
                h = min(SUBSTEP_S, stop_at - t)
                nx, ny, yaw = self._step(x, y, yaw, vx, vy, vyaw, h)
                travelled += math.hypot(nx - x, ny - y)
                x, y = nx, ny
                t += h
            else:
                t += SUBSTEP_S
        if t_end is not None and t_end <= horizon_s:
            p.end, p.path_m = (x, y, yaw), travelled
        elif t_end is not None:  # roll on (unsampled) to the end of the command
            tt = horizon_s
            while tt < t_end - 1e-9:
                h = min(SUBSTEP_S, t_end - tt)
                nx, ny, yaw = self._step(x, y, yaw, vx, vy, vyaw, h)
                travelled += math.hypot(nx - x, ny - y)
                x, y = nx, ny
                tt += h
            p.end, p.path_m = (x, y, yaw), travelled
        p.zones = _dedup(zone_of(a, b) for a, b in zip(p.x, p.y, strict=False))
        return p

    def predict_goto(self, s, plan, horizon_s, dt, zone_of, cap_of, settle_s=0.6) -> Prediction:
        """Roll navigate_to's steering law (10 Hz control, 50 Hz integration) until arrival or max_s; the
        trajectory is sampled over the horizon, duration / end / path cover the whole plan."""
        p = Prediction(self.name, horizon_s)
        tx, ty = plan.goal
        target_zone = zone_of(tx, ty)
        x, y, yaw = s.x, s.y, s.yaw
        t, path, next_sample, arrived = 0.0, 0.0, 0.0, None
        vx = vyaw = 0.0
        ctrl_next = 0.0
        while t <= max(plan.max_s, horizon_s) + 1e-9:
            if arrived is None and t >= ctrl_next - 1e-9:
                ctrl_next += 0.1
                d = math.hypot(tx - x, ty - y)
                if d < plan.tol_m or t >= plan.max_s:
                    arrived = t
                    vx = vyaw = 0.0
                else:
                    err = _wrap(math.atan2(ty - y, tx - x) - yaw)
                    if abs(err) > 0.35:
                        vx, vyaw = 0.0, max(-0.6, min(0.6, 1.5 * err))
                    else:
                        z = zone_of(x, y)
                        vx = min(cap_of(target_zone), cap_of(z)) * self.speed_scale
                        vyaw = max(-0.4, min(0.4, 1.0 * err))
                    if t <= horizon_s:
                        p.speed_mps = max(p.speed_mps, abs(vx))
                        p.yaw_rate = max(p.yaw_rate, abs(vyaw))
            if t <= horizon_s + 1e-9 and t >= next_sample - 1e-9:
                p.t.append(round(t, 4))
                p.x.append(x)
                p.y.append(y)
                p.yaw.append(yaw)
                p.v.append(abs(vx))
                next_sample += dt
            if arrived is not None and t > horizon_s:
                break
            nx, ny, yaw = self._step(x, y, yaw, vx, 0.0, vyaw, SUBSTEP_S)
            path += math.hypot(nx - x, ny - y)
            x, y = nx, ny
            if arrived is None:
                p.end = (x, y, yaw)
            t += SUBSTEP_S
        p.duration_s = (arrived if arrived is not None else plan.max_s) + settle_s
        p.end = p.end or (x, y, yaw)
        p.path_m = path
        p.zones = _dedup(zone_of(a, b) for a, b in zip(p.x, p.y, strict=False))
        return p


def _dedup(seq) -> list[str]:
    out: list[str] = []
    for z in seq:
        if not out or out[-1] != z:
            out.append(z)
    return out
