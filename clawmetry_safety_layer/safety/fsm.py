"""Safety state machine: NORMAL -> CAUTION -> HOLD -> RECOVER -> NORMAL.

Every layer reports *causes*, each with a source (L1 geofence prediction | L2 perception | L3 Cosmos judge | link |
operator), a level (caution | hold), a reason, a policy flag and an optional time-to-live:

  * CAUTION while any caution cause is live: speed capped to caution_speed (0.15 m/s).
  * HOLD while any hold cause is live: no motion; the running skill is stopped (the supervisor does that).
    - non-policy holds (a person closer than 1 m) expire with their ttl: the FSM falls back by itself.
    - policy holds (predicted / judged / seen go-zone breach, operator Stop) LATCH: they end only through
      reset() -> RECOVER (recover_to_go_zone, bounded) -> approve() (ClawMetry Approve, or the console's Approve
      with the edge key). NORMAL comes only after that approval when the hold was policy-related.
  * RECOVER: recovery motion / waiting for approval. Nothing else moves.
Every transition is a journal event `fsm.transition` {from, to, reason, source}. Pure Python, thread-safe; time is
injectable for tests.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

NORMAL, CAUTION, HOLD, RECOVER = "NORMAL", "CAUTION", "HOLD", "RECOVER"
SOURCES = ("L1", "L2", "L3", "link", "operator")


class SafetyHold(Exception):
    """Raised into a running skill when the FSM does not allow motion."""


@dataclass
class Cause:
    key: str
    source: str
    level: str  # caution | hold
    reason: str
    policy: bool
    t: float
    until: float | None  # expiry (None = until cleared / approved)


class SafetyFSM:
    def __init__(self, emit=None, caution_speed: float = 0.15, clock=time.time):
        self.emit = emit or (lambda kind, **d: None)
        self.caution_speed = float(caution_speed)
        self.clock = clock
        self._causes: dict[str, Cause] = {}
        self._lock = threading.RLock()
        self._state = NORMAL
        self._latched: Cause | None = None  # the policy hold that needs reset + approval
        self._recovering = False
        self.awaiting_approval = False
        self.listeners: list = []  # fn(old, new, reason, source)

    # ------------------------------------------------------------ causes
    def raise_cause(
        self, key: str, source: str, level: str, reason: str, policy: bool = False, ttl: float | None = None
    ):
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")
        if level not in ("caution", "hold"):
            raise ValueError("level must be caution | hold")
        now = self.clock()
        with self._lock:
            c = self._causes.get(key)
            if c is None or c.level != level or c.reason != reason:
                c = Cause(key, source, level, reason, policy, now, None)
            c.until = None if ttl is None else now + ttl
            self._causes[key] = c
            if level == "hold" and policy and self._latched is None:
                self._latched = c
                self.awaiting_approval = False
            return self._update(c.reason, c.source)

    def clear_cause(self, key: str, reason: str = "cleared"):
        with self._lock:
            c = self._causes.pop(key, None)
            return self._update(reason, c.source if c else "operator")

    def causes(self) -> list[dict]:
        with self._lock:
            self._expire()
            return [
                {"key": c.key, "source": c.source, "level": c.level, "reason": c.reason, "policy": c.policy}
                for c in self._causes.values()
            ]

    # ------------------------------------------------------------ operator path
    def reset(self, source: str = "operator") -> bool:
        """ClawMetry Reset / console: leave HOLD for RECOVER. Returns True when recovery (or approval) is needed."""
        with self._lock:
            if self._latched is None:
                # nothing latched: drop non-policy holds the operator has looked at, back to NORMAL/CAUTION
                for k in [k for k, c in self._causes.items() if c.level == "hold"]:
                    self._causes.pop(k)
                self._update("reset by operator", source)
                return False
            # the latched cause (and any operator stop) stops counting as live; the robot is now in recovery
            for k in [k for k, c in self._causes.items() if c.level == "hold"]:
                self._causes.pop(k)
            self._recovering = True
            self._update(f"reset: recovering from {self._latched.reason}", source)
            return True

    def recovered(self, ok: bool, detail: str = "") -> None:
        """Recovery finished (inside the go zone with margin, or nothing to do). Policy holds then wait for approval."""
        with self._lock:
            if not ok:
                self._recovering = False
                lat = self._latched
                if lat is not None:
                    self._causes[lat.key] = Cause(
                        lat.key, lat.source, "hold", f"recovery failed: {detail}", True, self.clock(), None
                    )
                self._update(f"recovery failed: {detail}", "operator")
                return
            if self._latched is not None:
                self.awaiting_approval = True
                self._update(f"recovered{': ' + detail if detail else ''}; waiting for approval", "operator")
            else:
                self._recovering = False
                self._update("recovered", "operator")

    def approve(self, by: str = "operator") -> bool:
        with self._lock:
            if self._latched is None:
                return False
            if not self.awaiting_approval:
                return False  # approve only after recovery: approving a robot outside its go zone is not a thing
            self._latched = None
            self.awaiting_approval = False
            self._recovering = False
            self._update(f"approved by {by}", "operator")
            return True

    def deny(self, by: str = "operator") -> None:
        with self._lock:
            if self._latched is not None:
                lat = self._latched
                self.awaiting_approval = False
                self._recovering = False
                self._causes[lat.key] = Cause(
                    lat.key, lat.source, "hold", f"{lat.reason} (resume denied by {by})", True, self.clock(), None
                )
                self._update(f"resume denied by {by}", "operator")

    # ------------------------------------------------------------ queries
    @property
    def state(self) -> str:
        with self._lock:
            self._update("expired", "operator")
            return self._state

    def allows_motion(self) -> bool:
        return self.state in (NORMAL, CAUTION)

    def speed_cap(self) -> float | None:
        st = self.state
        if st == NORMAL:
            return None
        if st == CAUTION:
            return self.caution_speed
        return 0.0

    def snapshot(self) -> dict:
        with self._lock:
            st = self.state
            return {
                "state": st,
                "speed_cap": self.speed_cap(),
                "causes": self.causes(),
                "latched": None
                if self._latched is None
                else {"source": self._latched.source, "reason": self._latched.reason},
                "awaiting_approval": self.awaiting_approval,
            }

    # ------------------------------------------------------------ internals
    def _expire(self):
        now = self.clock()
        for k in [k for k, c in self._causes.items() if c.until is not None and c.until <= now]:
            self._causes.pop(k)

    def _compute(self) -> str:
        self._expire()
        if self._recovering:
            return RECOVER
        if self._latched is not None or any(c.level == "hold" for c in self._causes.values()):
            return HOLD
        if any(c.level == "caution" for c in self._causes.values()):
            return CAUTION
        return NORMAL

    def _update(self, reason: str, source: str) -> str:
        new = self._compute()
        old = self._state
        if new != old:
            # name the cause that decided the new state when there is one
            top = None
            if new == HOLD:
                top = self._latched or next((c for c in self._causes.values() if c.level == "hold"), None)
            elif new == CAUTION:
                top = next((c for c in self._causes.values() if c.level == "caution"), None)
            if top is not None:
                reason, source = top.reason, top.source
            self._state = new
            self.emit("fsm.transition", **{"from": old, "to": new, "reason": reason[:200], "source": source})
            for fn in list(self.listeners):
                try:
                    fn(old, new, reason, source)
                except Exception as e:  # noqa: BLE001
                    print(f"[fsm] listener failed: {e}")
        return new
