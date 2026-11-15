"""
"Follow me" - leading a guest somewhere while checking they are still there.

Pure stdlib. The runner (escort.py) does the I/O; everything decidable is here
so it can be tested with the robot flat.

THE PROBLEM, stated plainly: THE CAMERA FACES FORWARD AND THE FOLLOWER IS
BEHIND. One body-mounted monocular, HFOV 122 deg, no pan-tilt head. While the
robot is leading, the person it is leading is in precisely the one place it
cannot see. "Keep an eye on them" is not a tuning problem, it is a geometry
problem, and no amount of model quality fixes it.

Three ways out, with the hardware as it is:

  1. LOOK BACK AT CHECKPOINTS  <- implemented here
     Walk a leg, stop, turn, confirm, turn back, carry on. Uses the forward
     gait that already works. Costs turning time and makes the escort stutter.

     A full 180 is NOT needed: at HFOV 122 the frame spans +/-61 deg, so a
     120 deg turn already brings straight-behind into view at the edge. That
     is a third off every check, and the checks are the whole cost.

  2. CRAB-WALK, facing across the direction of travel
     `move()` takes a sideways vy (0.1-1.0 m/s), so the robot can strafe while
     facing ~90 deg off its heading - guest in frame the whole way, no stutter.
     The catch: the direction of travel then sits at the extreme edge of a
     122 deg lens, where distortion is worst and the people-guard is least
     reliable. Trading forward visibility for rear visibility, on a robot
     walking towards stairs, is not obviously a win. Left as an option.

  3. A SECOND CAMERA on the spare USB port. The honest fix. ~30 GBP, and then
     none of this is necessary.

WHAT COUNTS AS "FOLLOWING": not presence - TREND. Someone who stops to read a
sign is still visible behind the robot at a growing range. So the range at each
checkpoint is kept and compared: holding steady means following, growing every
time means being left behind, and the robot should wait rather than march on
and arrive alone.

IDENTITY is deliberately weak. Appearance matching is a colour histogram
compared by cosine similarity - enough to prefer the right person out of two
candidates over a few seconds, nowhere near re-identification. At a spa
entrance with a dozen people about, the robot can be fooled. The failure is
tolerable in one direction only, and that asymmetry is baked in below: being
unsure means waiting and asking, never marching on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


def wrap180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def cosine(a, b) -> float:
    """Similarity of two appearance vectors. 0 when either is missing."""
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return num / (na * nb)


class Phase(Enum):
    IDLE = "idle"
    CLEARING = "clearing"      # making sure we can see where we are going
    WALKING = "walking"        # a leg in progress
    LOOKING_BACK = "looking_back"
    WAITING = "waiting"        # follower lagging or missing; asking them
    ARRIVED = "arrived"
    ABORTED = "aborted"


@dataclass
class Action:
    """What the runner should do next."""
    kind: str                       # walk|turn|say|wait|stop|done
    distance_cm: float = 0.0
    bearing_deg: float = 0.0        # absolute, dead-reckoned world frame
    text: str = ""
    seconds: float = 0.0
    why: str = ""

    def __repr__(self) -> str:
        bits = [self.kind]
        if self.kind == "walk":
            bits.append("%.0fcm" % self.distance_cm)
        if self.kind == "turn":
            bits.append("to %.0f deg" % self.bearing_deg)
        if self.kind == "say":
            bits.append(repr(self.text))
        if self.kind == "wait":
            bits.append("%.1fs" % self.seconds)
        return "Action(%s)" % " ".join(bits)


@dataclass
class EscortPolicy:
    leg_cm: float = 150.0           # distance per leg between checks
    check_every_legs: int = 1

    # A 120 deg turn puts straight-behind at the frame edge; 180 centres it but
    # costs half again as much turning. 135 is the compromise: comfortably
    # inside the usable part of the lens.
    look_back_deg: float = 135.0

    follow_min_m: float = 0.8       # closer than this and they are not following,
                                    # they are about to walk into the robot
    follow_max_m: float = 5.0       # beyond this we have lost them
    lagging_m: float = 3.0          # beyond this, slow down and wait
    appearance_min: float = 0.55    # below this, do not claim it is the same person

    wait_s: float = 5.0
    max_missed_checks: int = 2      # then give up rather than march on alone
    reacquire_grace_s: float = 8.0


@dataclass
class Candidate:
    """A person seen during a look-back. Mirrors guard_core.Person loosely."""
    range_m: float
    bearing_deg: float              # relative to where the robot is now facing
    height_m: float = 1.70
    appearance: tuple = ()


@dataclass
class Follower:
    range_m: float
    appearance: tuple = ()
    last_seen_t: float = 0.0
    history: list = field(default_factory=list)   # range at each successful check

    @property
    def trend(self) -> str:
        """Are they keeping up? Needs three checks to say anything."""
        h = self.history
        if len(h) < 3:
            return "unknown"
        if h[-1] > h[-2] > h[-3] and h[-1] - h[-3] > 0.6:
            return "falling_behind"
        if h[-1] < h[-2] < h[-3] and h[-3] - h[-1] > 0.6:
            return "catching_up"
        return "keeping_up"


class Escort:
    """Leg-by-leg escort with look-back verification.

    Drive it by calling next_action(), performing what it returns, and feeding
    back what happened (walked / turned / saw people). It never blocks and holds
    no I/O, which is why it can be tested exhaustively.
    """

    def __init__(self, ring=None, pol: EscortPolicy | None = None):
        self.ring = ring                      # awareness.VisibilityRing or None
        self.pol = pol or EscortPolicy()
        self.phase = Phase.IDLE
        self.heading_deg = 0.0                # dead-reckoned
        self.facing_deg = 0.0                 # where the body currently points
        self.target_cm = 0.0
        self.walked_cm = 0.0
        self.legs = 0
        self.missed = 0
        self.follower: Follower | None = None
        self.destination = ""
        self.note = ""
        self._wait_until = 0.0
        self._scan_queue: list[float] = []

    # ------------------------------------------------------------------
    def start(self, distance_cm: float, destination: str = "",
              follower: Candidate | None = None, now: float = 0.0,
              heading_deg: float = 0.0) -> None:
        self.phase = Phase.CLEARING
        self.heading_deg = self.facing_deg = heading_deg
        self.target_cm = distance_cm
        self.walked_cm = 0.0
        self.legs = 0
        self.missed = 0
        self.destination = destination
        self.note = "starting"
        self._scan_queue = []
        # Who we are leading, recorded while they are still in FRONT of us -
        # the only moment the camera can see them properly.
        self.follower = (Follower(range_m=follower.range_m,
                                  appearance=follower.appearance,
                                  last_seen_t=now,
                                  history=[follower.range_m])
                         if follower else None)

    def abort(self, why: str) -> Action:
        self.phase = Phase.ABORTED
        self.note = why
        return Action("stop", why=why)

    @property
    def remaining_cm(self) -> float:
        return max(0.0, self.target_cm - self.walked_cm)

    # ------------------------------------------------------------------
    def next_action(self, now: float, guard_allows_walk: bool = True,
                    guard_reason: str = "") -> Action:
        """The single decision point. Call in a loop."""
        if self.phase in (Phase.ARRIVED, Phase.ABORTED):
            return Action("done", why=self.note)

        # The people-guard outranks the escort, always. An escort in progress is
        # not a reason to walk into somebody.
        if not guard_allows_walk and self.phase in (Phase.CLEARING, Phase.WALKING):
            self.note = "held by the people-guard: %s" % guard_reason
            return Action("wait", seconds=0.5, why=self.note)

        if self.phase is Phase.WAITING:
            if now < self._wait_until:
                return Action("wait", seconds=self._wait_until - now,
                              why=self.note)
            # waited long enough - look again
            self.phase = Phase.LOOKING_BACK
            return self._look_back_action("checking again after waiting")

        if self.phase is Phase.LOOKING_BACK:
            return self._look_back_action(self.note)

        if self.phase is Phase.CLEARING:
            # Point the camera where we are about to walk, if we cannot already
            # vouch for it. This is the "get better visibility first" step.
            if self._scan_queue:
                aim = self._scan_queue[0]
                return Action("turn", bearing_deg=aim,
                              why="looking %.0f deg before committing"
                                  % wrap180(aim - self.heading_deg))
            if self.ring is not None:
                ok, why = self.ring.can_commit_to(
                    self.heading_deg, now, self.pol.leg_cm / 100.0)
                if not ok:
                    plan = self.ring.sweep_plan(self.heading_deg, now) \
                        or [self.heading_deg]
                    self._scan_queue = plan
                    self.note = "no usable view ahead: %s" % why
                    return Action("turn", bearing_deg=plan[0], why=self.note)
            # face the way we are going, then walk
            if abs(wrap180(self.facing_deg - self.heading_deg)) > 8.0:
                return Action("turn", bearing_deg=self.heading_deg,
                              why="back to the direction of travel")
            self.phase = Phase.WALKING
            leg = min(self.pol.leg_cm, self.remaining_cm)
            self.note = "leading %.0f cm" % leg
            return Action("walk", distance_cm=leg, bearing_deg=self.heading_deg,
                          why=self.note)

        if self.phase is Phase.WALKING:
            # the runner reports completion via walked(); if we are here the leg
            # has not been reported yet
            leg = min(self.pol.leg_cm, self.remaining_cm)
            return Action("walk", distance_cm=leg, bearing_deg=self.heading_deg,
                          why=self.note)

        return Action("wait", seconds=0.2, why="idle")

    def _look_back_action(self, why: str) -> Action:
        aim = wrap180(self.heading_deg + self.pol.look_back_deg)
        if abs(wrap180(self.facing_deg - aim)) > 10.0:
            return Action("turn", bearing_deg=aim, why="looking back - " + why)
        return Action("wait", seconds=0.3, why="settling before the look-back")

    # ------------------------------------------------------------------
    # feedback from the runner
    # ------------------------------------------------------------------
    def turned(self, bearing_deg: float) -> None:
        self.facing_deg = wrap180(bearing_deg)
        if self._scan_queue and \
                abs(wrap180(self._scan_queue[0] - bearing_deg)) <= 10.0:
            self._scan_queue.pop(0)

    def walked(self, distance_cm: float, now: float) -> None:
        self.walked_cm += max(0.0, distance_cm)
        self.legs += 1
        if self.remaining_cm <= 1.0:
            # Still verify before declaring success - arriving alone is the
            # failure this whole module exists to prevent.
            self.phase = Phase.LOOKING_BACK
            self.note = "final check at the destination"
            return
        if self.follower is not None and \
                self.legs % self.pol.check_every_legs == 0:
            self.phase = Phase.LOOKING_BACK
            self.note = "checking after %.0f cm" % distance_cm
        else:
            self.phase = Phase.CLEARING

    def saw(self, candidates: list[Candidate], now: float) -> Action:
        """Report what the camera sees during a look-back. Returns what to do."""
        if self.phase is not Phase.LOOKING_BACK:
            return Action("wait", seconds=0.1, why="not expecting a look-back")

        match, score = self._best_match(candidates)

        if match is not None:
            f = self.follower
            f.range_m = match.range_m
            f.last_seen_t = now
            f.history.append(match.range_m)
            if match.appearance:
                f.appearance = match.appearance
            self.missed = 0

            if match.range_m > self.pol.lagging_m or f.trend == "falling_behind":
                self.phase = Phase.WAITING
                self._wait_until = now + self.pol.wait_s
                self.note = ("follower %.1f m back and %s - waiting"
                             % (match.range_m, f.trend.replace("_", " ")))
                return Action("say", text="Take your time, I'll wait here.",
                              why=self.note)

            if self.remaining_cm <= 1.0:
                self.phase = Phase.ARRIVED
                self.note = ("arrived with the guest %.1f m behind"
                             % match.range_m)
                return Action("done", why=self.note)

            self.phase = Phase.CLEARING
            self.note = "follower %.1f m back, %s" % (match.range_m, f.trend)
            return Action("turn", bearing_deg=self.heading_deg, why=self.note)

        # nobody plausible behind us
        self.missed += 1
        if self.missed >= self.pol.max_missed_checks:
            self.phase = Phase.ABORTED
            self.note = ("lost the guest after %d checks - stopping rather "
                         "than arriving alone" % self.missed)
            return Action("say",
                          text="I've lost you - I'll wait right here.",
                          why=self.note)

        self.phase = Phase.WAITING
        self._wait_until = now + self.pol.wait_s
        self.note = "no one behind me (check %d)" % self.missed
        return Action("say", text="Still with me?", why=self.note)

    # ------------------------------------------------------------------
    def _best_match(self, candidates: list[Candidate]):
        """Pick the follower out of what is visible. None if unconvinced.

        Scored on range plausibility first, appearance as a tiebreak. When
        nothing scores well the answer is None - and None means wait and ask,
        never walk on.
        """
        if self.follower is None or not candidates:
            return None, 0.0
        expected = self.follower.range_m
        best, best_score = None, 0.0
        for c in candidates:
            if not (self.pol.follow_min_m <= c.range_m <= self.pol.follow_max_m):
                continue
            # 1.0 when exactly where we left them, decaying over 3 m of error
            near = max(0.0, 1.0 - abs(c.range_m - expected) / 3.0)
            look = cosine(self.follower.appearance, c.appearance)
            if self.follower.appearance and c.appearance:
                if look < self.pol.appearance_min and len(candidates) > 1:
                    # a clearly different-looking person, with alternatives
                    continue
                score = 0.5 * near + 0.5 * look
            else:
                score = near
            if score > best_score:
                best, best_score = c, score
        return (best, best_score) if best_score >= 0.25 else (None, best_score)

    # ------------------------------------------------------------------
    def as_dict(self) -> dict:
        f = self.follower
        return {
            "phase": self.phase.value,
            "destination": self.destination,
            "note": self.note,
            "walked_cm": round(self.walked_cm),
            "target_cm": round(self.target_cm),
            "remaining_cm": round(self.remaining_cm),
            "legs": self.legs,
            "missed_checks": self.missed,
            "heading_deg": round(self.heading_deg, 1),
            "facing_deg": round(self.facing_deg, 1),
            "follower": None if f is None else {
                "range_m": round(f.range_m, 2),
                "trend": f.trend,
                "checks": len(f.history),
            },
        }
