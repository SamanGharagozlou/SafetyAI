"""Dependency-free kinematic robot. Integrates commanded velocity in real time
so navigation timing, zones and settle behaviour are exercised for real."""

from __future__ import annotations

import math
import threading
import time

from . import ROOT
from .base import ARM_REST, RobotBridge, RobotState
from .zones import Zones


class FakeBridge(RobotBridge):
    body = "fake"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        x, y, yaw = cfg.get("start_pose", [1.0, 1.0, 0.0])
        self._s = RobotState(x=x, y=y, yaw=yaw, body=self.body, mode="idle")
        self._v = (0.0, 0.0, 0.0)
        self._lock = threading.Lock()
        self._zones = Zones(ROOT / cfg.get("zones_file", "policies/zones.yaml"))
        self._s.zone = self._zones.zone_of(x, y)
        self._settle_until = 0.0
        self._run = True
        self._t = threading.Thread(target=self._loop, daemon=True)

    def connect(self, iface: str, domain: int) -> None:
        self._t.start()

    def close(self) -> None:
        self._run = False

    def _loop(self):
        last = time.time()
        while self._run:
            time.sleep(0.02)
            now = time.time()
            dt, last = now - last, now
            with self._lock:
                vx, vy, vyaw = self._v
                s = self._s
                if vx or vy or vyaw:
                    s.yaw += vyaw * dt
                    s.yaw = (s.yaw + math.pi) % (2 * math.pi) - math.pi
                    s.x += (vx * math.cos(s.yaw) - vy * math.sin(s.yaw)) * dt
                    s.y += (vx * math.sin(s.yaw) + vy * math.cos(s.yaw)) * dt
                    s.mode = "walking"
                    s.balance_ok = True
                    s.battery_pct = max(0.0, s.battery_pct - 0.002 * dt * 100)
                elif s.mode == "walking":
                    s.mode = "settling"
                    self._settle_until = now + 0.6  # realistic settle time
                elif s.mode == "settling" and now >= self._settle_until:
                    s.mode = "standing"
                s.balance_ok = s.mode != "settling"
                s.zone = self._zones.zone_of(s.x, s.y)
                s.ts = now

    def state(self) -> RobotState:
        with self._lock:
            return RobotState(**{**self._s.__dict__, "arm_q": list(self._s.arm_q)})

    def stand(self) -> None:
        with self._lock:
            if self._s.mode in ("idle", "damped"):
                self._s.mode = "standing"

    def move_velocity(self, vx, vy, vyaw) -> None:
        with self._lock:
            self._v = (vx * self.speed_scale, vy * self.speed_scale, vyaw)

    def stop(self) -> None:
        with self._lock:
            self._v = (0.0, 0.0, 0.0)

    def damp(self) -> None:
        self.stop()
        with self._lock:
            self._s.mode = "damped"

    def set_arm_targets(self, q, ramp_s=2.0) -> None:
        def apply(qq):
            with self._lock:
                self._s.arm_q = list(qq)

        self._ramp(self.state().arm_q, list(q), ramp_s, apply)
        with self._lock:
            self._s.holding = None if list(q) == ARM_REST else self._s.holding
