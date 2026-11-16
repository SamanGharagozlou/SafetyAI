"""ROS-free gate between /follow/cmd_vel and /cmd_vel.

Silent unless armed AND the follow state is a fresh TRACKING AND the command is fresh.
Any loss of those conditions yields one zero command, then silence again, so the relay
does not fight other /cmd_vel writers while it is not driving.
"""

import math
from dataclasses import dataclass, replace

from .params import VX_CAP, WZ_CAP


@dataclass
class RelayParams:
    max_vx: float = 0.3          # below the follow controller's own 0.5 cap
    max_wz: float = 0.5
    cmd_timeout_s: float = 0.5   # /follow/cmd_vel older than this is not forwarded
    state_timeout_s: float = 1.5  # /follow/state is published at 2 Hz
    arm_timeout_s: float = 120.0  # arming expires; 0 = never


class RelayGate:
    def __init__(self, params: RelayParams) -> None:
        self.p = replace(params, max_vx=min(abs(params.max_vx), VX_CAP),
                         max_wz=min(abs(params.max_wz), WZ_CAP))
        self.armed = False
        self._armed_at = 0.0
        self._active = False  # last output was a non-silent motion command
        self._state = None
        self._state_t = None
        self._cmd = (0.0, 0.0)
        self._cmd_t = None

    def arm(self, now: float) -> None:
        self.armed, self._armed_at, self._active = True, now, False

    def disarm(self) -> None:
        self.armed = False

    def on_state(self, now: float, state: str) -> None:
        self._state, self._state_t = state, now

    def on_cmd(self, now: float, vx: float, wz: float) -> None:
        self._cmd, self._cmd_t = (vx, wz), now

    def _expire(self, now: float) -> None:
        if self.armed and self.p.arm_timeout_s > 0 and now - self._armed_at > self.p.arm_timeout_s:
            self.armed = False

    def state_fresh(self, now: float) -> bool:
        return self._state_t is not None and now - self._state_t <= self.p.state_timeout_s

    def reason(self, now: float) -> str:
        """'ok' when motion would be forwarded, else why not."""
        self._expire(now)
        if not self.armed:
            return 'disarmed'
        if not self.state_fresh(now):
            return 'stale state'
        if self._state != 'TRACKING':
            return f'state {self._state}'
        if self._cmd_t is None or now - self._cmd_t > self.p.cmd_timeout_s:
            return 'stale cmd'
        return 'ok'

    def output(self, now: float):
        """(vx, wz) to publish on /cmd_vel, or None to stay silent."""
        if self.reason(now) != 'ok':
            if self._active:
                self._active = False
                return 0.0, 0.0
            return None
        self._active = True
        vx, wz = (v if math.isfinite(v) else 0.0 for v in self._cmd)
        vx = max(0.0, min(vx, self.p.max_vx))  # never reverse
        wz = max(-self.p.max_wz, min(wz, self.p.max_wz))
        return vx, wz
