"""Continuous monitor: the behaviour policy checked against live state every period_s (default 200 ms),
independent of skill calls. Started by the skills API.

Checks: speed over the zone / holding / near-person / quiet-hours cap (soft above cap*(1+soft_margin), hard above
cap*(1+hard_margin)), entry into a forbidden zone (hard), carry height (hard), body tilt on bodies that report it
(hard). Every breach is a `breach` trace event (on the rising edge, then every 2 s while it lasts, and a
`breach` with active=false when it clears). A hard breach makes the running skill stop issuing commands
(Skills.monitor_stop) and calls bridge.stop_and_settle(): the body's own stop, never Damp, never the e-stop.
"""

from __future__ import annotations

import math
import threading
import time

REPEAT_S = 2.0
TELEPORT_M = 1.0  # a jump this large in one period is a sim reset / relocalisation, not a speed


class Monitor:
    def __init__(self, skills, period_s: float | None = None):
        self.sk = skills
        self.period_s = period_s
        self._stop = threading.Event()
        self._prev = None
        self._active: dict[str, dict] = {}  # rule key -> {since, last_emit, severity}
        self._last_hard_stop = 0.0
        self.ticks = 0
        self.hard_stops = 0
        self._t = threading.Thread(target=self._loop, daemon=True, name="behaviour-monitor")

    def start(self):
        self._t.start()
        return self

    def close(self):
        self._stop.set()
        if self._t.is_alive():
            self._t.join(timeout=2)

    def _loop(self):
        while not self._stop.is_set():
            period = self.period_s or float(self.sk.policy.monitor_cfg()["period_s"])
            t0 = time.time()
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001  (the monitor must keep running)
                print(f"[monitor] tick failed: {e.__class__.__name__}: {e}")
            self._stop.wait(max(0.0, period - (time.time() - t0)))

    # ---------------------------------------------------------------- checks
    def tick(self) -> list[dict]:
        self.ticks += 1
        sk, pol = self.sk, self.sk.policy
        mc = pol.monitor_cfg()
        s = sk.bridge.state()
        now = time.time()
        prev, self._prev = self._prev, (s.x, s.y, s.ts or now)
        speed = 0.0
        if prev is not None:
            dt = (s.ts or now) - prev[2]
            d = math.hypot(s.x - prev[0], s.y - prev[1])
            if dt > 1e-3 and d < TELEPORT_M:
                speed = d / dt / sk.time_compression
                tr = sk._turn
                if tr is not None:
                    tr["path_m"] = tr.get("path_m", 0.0) + d  # turn path length for the feedback event
        holding = bool(s.holding or sk._carrying)
        near = bool(getattr(sk.bridge, "near_person", lambda: False)())
        zone = pol.zones.zone_of(s.x, s.y)
        found: list[dict] = []
        cap = pol.speed_cap(zone, holding, near)
        if speed > cap * (1 + float(mc["soft_speed_margin"])):
            hard = speed > cap * (1 + float(mc["hard_speed_margin"]))
            found.append(
                {
                    "rule": "speed_while_holding" if holding else "speed_cap",
                    "severity": "hard" if hard else "soft",
                    "zone": zone,
                    "value": round(speed, 3),
                    "cap": round(cap, 3),
                    "detail": f"{speed:.2f} m/s over the {cap:.2f} m/s cap in {zone}",
                }
            )
        hh = sk.hold_height(s.arm_q) if holding else None
        recovering = bool(getattr(getattr(sk, "safety", None), "_recovering_motion", False))
        for b in pol.check_sample(s.x, s.y, 0.0, holding, hh, near):  # zone entry, carry height
            if recovering and b.get("rule") == "forbidden_zone":
                # the safety supervisor is backing the robot OUT of this zone (bounded, 0.15 m/s, after Reset):
                # still reported, but stopping it would keep the robot where it must not be
                b = {**b, "severity": "soft", "detail": b.get("detail", "") + " (recovering: backing out)"}
            found.append(b)
        tilt_fn = getattr(sk.bridge, "tilt_deg", None)
        tilt = tilt_fn() if callable(tilt_fn) else None
        max_tilt = float(pol.raw["forbidden"].get("max_tilt_deg", 90))
        if tilt is not None and tilt > max_tilt:
            found.append(
                {
                    "rule": "tilt",
                    "severity": "hard",
                    "value": round(tilt, 1),
                    "cap": max_tilt,
                    "detail": f"body tilt {tilt:.1f} deg over {max_tilt:.0f} deg",
                }
            )
        keys = set()
        hard = [b for b in found if b["severity"] == "hard"]
        for b in found:
            key = f"{b['rule']}:{b.get('zone', '')}"
            keys.add(key)
            a = self._active.get(key)
            if a is None or now - a["last_emit"] >= REPEAT_S or (b["severity"] == "hard" and a["severity"] != "hard"):
                act = "stop_and_settle" if b in hard else "none"
                sk.emit(
                    "breach",
                    source="monitor",
                    active=True,
                    action=act,
                    **b,
                    state={
                        "x": round(s.x, 3),
                        "y": round(s.y, 3),
                        "yaw": round(s.yaw, 3),
                        "zone": zone,
                        "mode": s.mode,
                        "holding": s.holding,
                    },
                )
                self._active[key] = {"since": a["since"] if a else now, "last_emit": now, "severity": b["severity"]}
        for key in [k for k in self._active if k not in keys]:
            a = self._active.pop(key)
            sk.emit(
                "breach",
                source="monitor",
                active=False,
                rule=key.split(":")[0],
                zone=key.split(":")[1] or None,
                severity=a["severity"],
                duration_s=round(now - a["since"], 3),
                detail="cleared",
            )
        if hard and now - self._last_hard_stop > 1.0:
            self._last_hard_stop = now
            self.hard_stops += 1
            reason = "; ".join(b["detail"] for b in hard)
            sk.monitor_stop(reason)  # the running skill stops issuing commands ...
            sk.bridge.stop_and_settle()  # ... and the body stops through its own controller
        return found
