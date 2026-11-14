"""L1 geofence: the go zone is the union of the zones the behaviour policy allows (not restricted, not camera_off).
The command gate's kinematic predictor (bridge/predictor.py) rolls every velocity command 2 s ahead
(policies/behaviour_therme.yaml prediction.horizon_s); any predicted sample outside the go zone is a
breach_predicted(source=L1) and the command is never sent.
"""

from __future__ import annotations

import math


class GoZone:
    def __init__(self, zones, policy):
        self.zones = zones
        self.allowed = [n for n in zones.names() if not policy.forbidden_zone(n)]

    def contains(self, x: float, y: float) -> bool:
        return self.zones.zone_of(x, y) in self.allowed

    def inside_with_margin(self, x: float, y: float, margin: float, n: int = 16) -> bool:
        """The centre and a ring of n points at `margin` are all inside the go zone (works across shared edges of
        adjacent zones, unlike a per-polygon edge distance)."""
        if not self.contains(x, y):
            return False
        return all(
            self.contains(x + margin * math.cos(2 * math.pi * i / n), y + margin * math.sin(2 * math.pi * i / n))
            for i in range(n)
        )

    def describe(self) -> list[str]:
        return list(self.allowed)


class Geofence:
    def __init__(self, go: GoZone):
        self.go = go

    def check(self, pred) -> list[dict]:
        """Breaches of the go zone in a Prediction (first exit only). Starting outside: moving further out is a
        breach, moving back in is not (recovery)."""
        xs, ys, ts = pred.x, pred.y, pred.t
        if not xs:
            return []
        if not self.go.contains(xs[0], ys[0]):
            return []  # already outside: recovery logic owns this; the forbidden-zone check still applies
        for t, x, y in zip(ts, xs, ys, strict=False):
            if not self.go.contains(x, y):
                return [
                    {
                        "rule": "leaves_go_zone",
                        "severity": "hard",
                        "source": "L1",
                        "t": round(t, 2),
                        "at": [round(x, 2), round(y, 2)],
                        "zone": self.go.zones.zone_of(x, y),
                        "detail": f"predicted to leave the go zone in {t:.1f} s (into {self.go.zones.zone_of(x, y)})",
                    }
                ]
        return []
