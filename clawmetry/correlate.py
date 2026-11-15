"""Turns a stream of ledger events into breaches.

This is the part that makes the record *oversight* rather than logging. A log
says what each component reported. Oversight says whether those reports agree
with each other and with the robot's measured behaviour.

Every detector here answers a question no single component can answer alone,
because each one needs two sources that do not trust each other:

  unattributed_motion   the robot moved and no authorised intent explains it
  gate_bypass           a command reached the robot the gate never saw
  narration_divergence  the agent told a guest it was moving; it was not
  prediction_deviation  it moved, but not to where the world model said it would
  policy_breach         confirmed state violates a cap that was supposed to hold
  silence               a source stopped reporting

SILENCE IS A FINDING, NOT A GAP. This project has twice shipped a system whose
failure mode looked like calm: the public endpoint that answered 200 with nulls
while the robot lay on the floor, and the camera guard's "no fresh frame ->
FREEZE". A monitor that renders a missing feed as an empty chart is worse than
no monitor, because somebody trusts it. So absence emits an event.

Pure stdlib, no I/O, no clock of its own - `now` is always passed in. That is
what lets the whole detector set be tested on a laptop while the robot is flat,
which is most of the time.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

# Which tool names are claims of physical movement. Used by narration
# divergence: these are the ones a guest can watch and catch you lying about.
MOTION_INTENTS = {
    "walk_distance", "move_robot", "guide_to", "navigate_to",
    "go_to_place", "escort_to", "lead_to",
}

# Commands that are always allowed through un-gated, because gating a stop is
# how you get a robot that cannot be stopped. Mirrors tools/robot-guard.
UNGATED = {"stop_robot", "stop", "pause", "estop", "robot_status", "get_state"}


@dataclass
class Policy:
    # a confirmed speed above this with people about is a breach regardless of
    # what any component believed at the time
    speed_cap_mps: float = 0.4
    person_min_m: float = 1.0

    # CHILDREN. The robot is waist-height to an adult and head-height to a
    # toddler, and the handshake gesture raises a paw to roughly the head
    # height of a two-year-old. These were Python defaults in
    # tools/robot-guard until the venue file made them the operator's to set.
    child_freeze_m: float = 2.0
    child_height_under_m: float = 1.25

    # how long after an authorising intent a motion still counts as explained
    attribution_window_s: float = 8.0

    # the agent said it was moving; this long without measured motion is a lie
    narration_grace_s: float = 6.0

    # no state / no frame for this long and the feed is presumed dead
    silence_s: float = 2.0

    # deviation z above this is a breach, not a note. Matches the escalation
    # point used by the edge's own anomaly scoring.
    deviation_z_breach: float = 3.0

    # below this a confirmed velocity is noise, not motion
    motion_eps_mps: float = 0.03


@dataclass
class Open:
    """An authorised action in flight."""
    corr: str
    tool: str
    args: dict
    t_intent: float
    decided: bool = False
    decision: str = ""          # allow | deny | hold
    t_decision: float = 0.0
    commanded: bool = False
    t_command: float = 0.0
    prediction: dict | None = None
    moved: bool = False
    t_last_motion: float = 0.0


@dataclass
class Correlator:
    pol: Policy = field(default_factory=Policy)

    open: dict = field(default_factory=dict)        # corr -> Open
    _last_state_t: float = 0.0
    _last_seen: dict = field(default_factory=dict)  # source -> last ts, or None if never
    _motion_observable: bool = False                # have we EVER had a usable motion signal
    _expected_at: dict = field(default_factory=dict)
    _silent: set = field(default_factory=set)       # sources already reported silent
    _moving_since: float = 0.0
    _fired: set = field(default_factory=set)        # (rule, corr) already reported

    # ------------------------------------------------------------------
    # feed events in; get breaches out. Never raises on unknown input: a
    # malformed event from one collector must not blind the other five.
    # ------------------------------------------------------------------
    startup_grace_s: float = 10.0

    def expect(self, source: str, now: float = 0.0):
        """Declare a feed that should exist. Never arriving is itself a finding.

        The grace matters: without it the first tick fires 'never reported'
        half a second after launch, before the collector has made its first
        HTTP call, and the dashboard opens covered in red.
        """
        self._last_seen.setdefault(source, None)
        self._expected_at.setdefault(source, now or time.time())

    def feed(self, ev: dict) -> list[dict]:
        kind = ev.get("kind")
        data = ev.get("data") or {}
        ts = float(ev.get("ts") or 0.0)
        corr = ev.get("corr") or ""
        # Only HEARTBEAT sources are watched for silence. A `state` sample (or
        # anything explicitly flagged) is a feed that should keep arriving; an
        # intent or a command is a one-off, and a quiet agent is not a fault.
        if kind == "state" or data.get("heartbeat"):
            src = data.get("source") or ev.get("actor") or "unknown"
            self._last_seen[src] = ts
            self._silent.discard(src)
            # Can we tell motion from this feed at all? A feed with none of
            # these cannot support "it did not move" - only "we cannot say".
            if ("moving" in data or data.get("speed_mps") is not None
                    or data.get("has_pose")):
                self._motion_observable = True

        fn = getattr(self, f"_on_{kind}", None)
        return fn(ev, data, ts, corr) if fn else []

    # -- per-kind handlers ----------------------------------------------

    def _on_intent(self, ev, data, ts, corr) -> list[dict]:
        tool = data.get("tool", "")
        if corr:
            self.open[corr] = Open(corr=corr, tool=tool, args=data.get("args") or {}, t_intent=ts)
        return []

    def _on_prediction(self, ev, data, ts, corr) -> list[dict]:
        o = self.open.get(corr)
        if o:
            o.prediction = data
        out = []
        # The world model said this action breaches BEFORE it runs. The deck's
        # "even a misfire cannot move the robot into a restricted zone".
        if data.get("predicted_breach"):
            out.append(self._breach(
                "predicted_breach", ev, severity="high",
                detail=data.get("reason", "the predicted path violates policy"),
                extra={"basis": data.get("basis"), "zones": data.get("zones_crossed")}))
        return out

    def _on_decision(self, ev, data, ts, corr) -> list[dict]:
        o = self.open.get(corr)
        if o:
            o.decided = True
            o.decision = data.get("decision", "")
            o.t_decision = ts
        return []

    def _on_command(self, ev, data, ts, corr) -> list[dict]:
        tool = data.get("tool", "")
        o = self.open.get(corr)
        if o:
            o.commanded = True
            o.t_command = ts
        out = []
        if tool in UNGATED:
            return out
        # A command the gate never adjudicated reached the robot. Either a
        # component is publishing around the gate, or the gate was bypassed.
        if o is None or not o.decided:
            out.append(self._breach(
                "gate_bypass", ev, severity="high",
                detail=f"{tool or 'a command'} reached the robot with no gate decision",
                extra={"tool": tool}))
        elif o.decision == "deny":
            out.append(self._breach(
                "denied_command_executed", ev, severity="critical",
                detail=f"{tool} was denied and was sent anyway", extra={"tool": tool}))
        return out

    def _on_state(self, ev, data, ts, corr) -> list[dict]:
        self._last_state_t = ts
        out = []
        speed = self._speed(data)
        moving = bool(data.get("moving")) or speed > self.pol.motion_eps_mps

        if moving:
            self._moving_since = self._moving_since or ts
            owner = self._attributing(ts)
            if owner is None:
                out.append(self._breach(
                    "unattributed_motion", ev, severity="critical",
                    detail=(f"robot moving at {speed:.2f} m/s with no authorised intent "
                            f"in the last {self.pol.attribution_window_s:g}s"),
                    extra={"speed_mps": round(speed, 3)}))
            else:
                owner.moved = True
                owner.t_last_motion = ts
        else:
            self._moving_since = 0.0

        if speed > self.pol.speed_cap_mps:
            out.append(self._breach(
                "policy_breach", ev, severity="high",
                detail=f"confirmed speed {speed:.2f} m/s over the {self.pol.speed_cap_mps:g} m/s cap",
                extra={"policy": "speed_cap", "value": round(speed, 3), "cap": self.pol.speed_cap_mps}))

        # A child inside the freeze radius means stop, not slow. Movement of any
        # kind near a small child is the scenario the whole guard exists for.
        if data.get("child_near") and moving:
            out.append(self._breach(
                "child_proximity", ev, severity="critical",
                detail=(f"moving with a child within {self.pol.child_freeze_m:g} m - "
                        f"the rule is a full stop, not a slow-down"),
                extra={"policy": "child_freeze", "speed_mps": round(speed, 3),
                       "nearest_person_m": data.get("nearest_person_m")}))

        near = data.get("nearest_person_m")
        if near is not None and near < self.pol.person_min_m and moving:
            out.append(self._breach(
                "policy_breach", ev, severity="critical",
                detail=f"moving with a person {near:.2f} m away (minimum {self.pol.person_min_m:g} m)",
                extra={"policy": "person_distance", "value": near, "cap": self.pol.person_min_m}))
        return out

    def _on_outcome(self, ev, data, ts, corr) -> list[dict]:
        o = self.open.pop(corr, None)
        out = []
        z = data.get("deviation_z")
        if z is None and data.get("tool") not in (None, ""):
            out.append(self._breach(
                "unverified_action", ev, severity="medium",
                detail=("the action completed but could not be checked against its "
                        "prediction - no pose feed and no calibrated baseline"),
                extra={"tool": data.get("tool")}))
        if z is not None and z >= self.pol.deviation_z_breach:
            out.append(self._breach(
                "prediction_deviation", ev, severity="high",
                detail=(f"outcome deviates {z:.1f} sigma from the predicted end state "
                        f"- it moved, but not where the model said"),
                extra={"deviation_z": z, "basis": data.get("basis")}))
        # It claimed motion to a guest and never measurably moved.
        if o and o.tool in MOTION_INTENTS and not o.moved:
            # Only a feed that COULD have shown motion can prove its absence.
            # Without one this is our blindness, not the agent's dishonesty -
            # a real 95 cm walk was twice called a lie because the state
            # collector was 401ing and nothing had noticed.
            if self._motion_observable:
                out.append(self._breach(
                    "narration_divergence", ev, severity="medium",
                    detail=f"{o.tool} completed with no measured motion at any point",
                    extra={"tool": o.tool}))
            else:
                out.append(self._breach(
                    "unverified_action", ev, severity="medium",
                    detail=f"{o.tool} completed with no motion feed to check it against",
                    extra={"tool": o.tool}))
        return out

    def _on_utterance(self, ev, data, ts, corr) -> list[dict]:
        return []

    def _on_intervention(self, ev, data, ts, corr) -> list[dict]:
        return []

    def _on_breach(self, ev, data, ts, corr) -> list[dict]:
        return []

    def _on_integrity(self, ev, data, ts, corr) -> list[dict]:
        return []

    # -- time-driven checks ---------------------------------------------

    def tick(self, now: float) -> list[dict]:
        """Call on a timer. Detects the things defined by absence."""
        out = []
        for src, last in self._last_seen.items():
            if src in self._silent:
                continue
            if last is None:
                if now - self._expected_at.get(src, now) < self.startup_grace_s:
                    continue
                self._silent.add(src)
                out.append(self._synth(
                    "source_never_reported", severity="high",
                    detail=(f"collector '{src}' was declared but has never produced a "
                            f"sample - everything that depends on it is unverified"),
                    extra={"source": src}, now=now))
                continue
            if now - last > self.pol.silence_s:
                self._silent.add(src)
                out.append(self._synth(
                    "silence", severity="high",
                    detail=f"no event from {src} for {now - last:.1f}s - treat as unobserved, not as clear",
                    extra={"source": src, "age_s": round(now - last, 2)}, now=now))

        for o in list(self.open.values()):
            if (self._motion_observable
                    and o.tool in MOTION_INTENTS and o.commanded and not o.moved
                    and now - o.t_command > self.pol.narration_grace_s
                    and ("narration_divergence", o.corr) not in self._fired):
                self._fired.add(("narration_divergence", o.corr))
                out.append(self._synth(
                    "narration_divergence", severity="medium",
                    detail=(f"{o.tool} was accepted {now - o.t_command:.0f}s ago and the robot "
                            f"has not measurably moved - the guest is being told otherwise"),
                    extra={"tool": o.tool}, corr=o.corr, now=now))
        return out

    # -- helpers ---------------------------------------------------------

    def _attributing(self, ts: float) -> Open | None:
        """The most recent allowed intent that could explain motion now."""
        best = None
        for o in self.open.values():
            if o.decision == "deny":
                continue
            if ts - o.t_intent > self.pol.attribution_window_s and not o.moved:
                continue
            if o.moved and ts - o.t_last_motion > self.pol.attribution_window_s:
                continue
            if best is None or o.t_intent > best.t_intent:
                best = o
        return best

    @staticmethod
    def _speed(data: dict) -> float:
        if "speed_mps" in data and data["speed_mps"] is not None:
            return abs(float(data["speed_mps"]))
        vx, vy = float(data.get("vx") or 0.0), float(data.get("vy") or 0.0)
        return math.hypot(vx, vy)

    def _breach(self, rule, ev, severity, detail, extra=None) -> dict:
        # extra FIRST: the canonical fields must win. An earlier version spread
        # extra last, so a policy breach carrying {"rule": "speed_cap"} silently
        # renamed itself and no dashboard filter on "policy_breach" matched it.
        return {**(extra or {}), "rule": rule, "severity": severity, "detail": detail,
                "corr": ev.get("corr", ""), "ts": ev.get("ts"), "from_seq": ev.get("seq")}

    def _synth(self, rule, severity, detail, extra=None, corr="", now=0.0) -> dict:
        return {**(extra or {}), "rule": rule, "severity": severity, "detail": detail,
                "corr": corr, "ts": round(now, 3), "from_seq": None}
