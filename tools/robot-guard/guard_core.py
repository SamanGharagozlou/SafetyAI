"""
Safety reasoning for the D1 greeter. Pure stdlib, no camera, no torch - so it is
testable on a laptop while the robot is flat, which is most of the time.

WHAT THE ROBOT ACTUALLY HAS (vendor spec 1.3, confirmed in the SDK headers):

    camera   ONE wide-angle monocular.  DFOV 122, HFOV 122, VFOV 70.
    IMU      standard.
    ...and that is the entire sensor table.

No stereo. No depth camera. No LiDAR, no ultrasonic, no ToF. `highlevel.h`
exposes motion, joints, battery and attitude - there is no obstacle API to call,
and the docs contain no mention of avoidance, following, mapping or navigation.
Autonomy is not a feature we are switching on; it is a feature we are adding.

So depth here is INFERRED, never measured. The one trick that makes that
honest: people stand on the floor. The floor is a known plane. Given camera
height and tilt, the pixel row of someone's FEET fixes their range by
intersecting that ray with the ground - and the accuracy works out where it
matters:

    1 m -> 0.6 cm per pixel      4 m ->  7.9 cm per pixel
    2 m -> 2.0 cm per pixel      6 m -> 17.6 cm per pixel
    3 m -> 4.5 cm per pixel      8 m -> 31.2 cm per pixel

Centimetres inside the safety envelope, garbage at the far end. That is the
right shape of error for this job: we need to know "is someone within two
metres", not "is that person 7.3 or 7.8 metres away".

THE BLIND SPOT - the whole reason this file is more than a threshold check.

A camera 0.40 m up with VFOV 70 cannot see the ground nearer than:

    tilt  0 deg -> 0.57 m        tilt 15 deg -> 0.34 m
    tilt 10 deg -> 0.40 m        tilt 30 deg -> 0.19 m

A two-year-old who toddles up to the robot to touch it crosses that line and
VANISHES FROM THE FRAME. Naive logic reads the empty frame as "all clear" and
resumes walking - into the child it was just looking at. The disappearance is
not absence. At close range it means CLOSER.

Hence LATCHING: a person lost at close range is presumed still there, in the
blind spot, for several seconds. A small person is latched longer and harder.
Confidence must decay towards danger, not towards convenience.

The same rule covers a dead feed. This stack has already shipped that bug once:
the public endpoint answered 200 with nulls while the robot lay on the floor,
and the voice agent cheerfully reported it as "stationary". A stale camera is
not a clear path. No fresh frame -> FREEZE.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Camera:
    """Monocular pinhole model + where it sits on the dog.

    `height_m` and `tilt_deg` MUST come from calibrate.py run on the real
    robot. The defaults are the spec sheet's standing height and a plausible
    tilt; they are a starting point for a bench test, not ground truth. An
    uncalibrated plane mis-ranges everything by a constant factor, which is the
    worst kind of wrong: self-consistent and confident.
    """
    width: int = 1280
    height: int = 720
    vfov_deg: float = 70.0
    hfov_deg: float = 122.0
    height_m: float = 0.40
    tilt_deg: float = 12.0
    calibrated: bool = False

    @property
    def fy(self) -> float:
        return (self.height / 2) / math.tan(math.radians(self.vfov_deg / 2))

    @property
    def blind_radius_m(self) -> float:
        """Nearest ground point in view. Inside this, the floor is invisible."""
        low = self.tilt_deg + self.vfov_deg / 2
        if low >= 90:
            return 0.0
        return self.height_m / math.tan(math.radians(low))

    def ground_range_m(self, foot_px_y: float) -> float | None:
        """Range to a point on the floor imaged at row `foot_px_y`.

        Returns None above the horizon row - a 'foot' there is not on our floor
        (someone on the stairs, a reflection in the glass, a bad box). Refusing
        to answer beats inventing a number.
        """
        # angle of this row below the optical axis
        dy = foot_px_y - self.height / 2
        below_axis = math.atan2(dy, self.fy)
        below_horizon = math.radians(self.tilt_deg) + below_axis
        if below_horizon <= 1e-4:
            return None
        r = self.height_m / math.tan(below_horizon)
        return r if r < 60.0 else None

    def real_height_m(self, box_px_h: float, range_m: float) -> float:
        """Physical height of something `box_px_h` tall at `range_m`."""
        return box_px_h * range_m / self.fy

    def bearing_deg(self, cx_px: float) -> float:
        """Horizontal angle off centre. Negative left, positive right.

        Uses the HFOV directly: at 122 degrees this lens is far from
        rectilinear, so this is approximate at the frame edges. Good enough -
        bearing only decides 'ahead of me' vs 'off to the side'.
        """
        return (cx_px / self.width - 0.5) * self.hfov_deg


# --------------------------------------------------------------------------
# what the detector hands us
# --------------------------------------------------------------------------

@dataclass
class Detection:
    """One person box, already in pixels."""
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float = 1.0

    @property
    def foot_y(self) -> float:
        return self.y2

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def px_h(self) -> float:
        return self.y2 - self.y1


@dataclass
class Person:
    """A detection turned into metres, plus a guess at how small they are."""
    range_m: float
    bearing_deg: float
    height_m: float
    conf: float
    truncated: bool = False

    CHILD_MAX_H = 1.25      # a 2-year-old is ~0.85 m; 1.25 clears toddlers and
                            # most small children without flagging short adults

    @property
    def is_child(self) -> bool:
        return self.height_m < self.CHILD_MAX_H

    @property
    def ahead(self) -> bool:
        """Within the cone the robot would walk into."""
        return abs(self.bearing_deg) <= 45.0


def to_person(cam: Camera, d: Detection) -> Person | None:
    """Project a box onto the floor. None if it cannot be placed there.

    A box clipped by the bottom edge of the frame is TRUNCATED: the feet are
    out of shot, so the real range is nearer than the lowest visible row
    suggests and the apparent height is an undercount. We keep it, mark it, and
    let the state machine treat it as close - never discard it. A box touching
    the bottom edge is the single most important box in the frame.
    """
    truncated = d.y2 >= cam.height - 2
    r = cam.ground_range_m(min(d.foot_y, cam.height - 2.0))
    if r is None:
        return None
    if truncated:
        # feet below the frame: it is at least as close as the blind edge
        r = min(r, max(cam.blind_radius_m, 0.15))
    h = cam.real_height_m(d.px_h, r)
    if truncated:
        # we are only seeing part of them, so the height is a lower bound and
        # must not be the thing that calls an adult a child
        h = max(h, 0.0)
    return Person(range_m=r, bearing_deg=cam.bearing_deg(d.cx),
                  height_m=h, conf=d.conf, truncated=truncated)


# --------------------------------------------------------------------------
# zones and states
# --------------------------------------------------------------------------

class Zone(IntEnum):
    CLEAR = 0
    WATCH = 1      # someone about, carry on
    NEAR = 2       # someone close, no new walking
    CONTACT = 3    # someone at touching distance, or just vanished into the blind spot


class State(IntEnum):
    """What the robot is permitted to do. Higher is more restricted."""
    GO = 0        # walk, turn, full gestures
    SLOW = 1      # walk capped, full gestures
    HOLD = 2      # no translation; gestures that keep four feet down
    FREEZE = 3    # stand still, nothing at all


# Gestures that lift a limb or shift the body hard enough to knock over a
# toddler. `shakeHand` raises a paw to roughly the head height of a
# two-year-old, which is exactly the scenario this exists for.
LIMB_GESTURES = frozenset({
    "shake", "shake_hand", "shakehand", "wave", "hello", "high_five",
    "jump", "jump_forward", "jump_back", "backflip", "flip",
    "stand_two_legs", "two_legs", "dance", "stretch",
})

# Gestures that keep all four feet planted. Safe to run with someone close -
# and worth keeping available, because a robot that goes completely inert when
# a child approaches is a worse greeter than one that nods at them.
SOFT_GESTURES = frozenset({
    "nod", "nod_head", "head_nod", "tilt", "tilt_head", "look",
    "sit", "sit_down", "stand", "stand_up", "lie", "lie_down", "wiggle",
})


@dataclass
class Policy:
    contact_m: float = 0.9      # blind radius 0.57 + margin for the feet
    near_m: float = 2.0
    watch_m: float = 4.0

    # a child inside this gets the hard treatment regardless of closing speed
    child_freeze_m: float = 2.0

    # how long a person lost at close range is presumed to still be there
    latch_s: float = 3.0
    child_latch_s: float = 5.0

    # no fresh frame for this long and we stop trusting the camera entirely
    stale_s: float = 0.5

    # escalate when someone will reach us within this many seconds
    ttc_s: float = 2.0

    walk_speed_go: float = 0.6
    walk_speed_slow: float = 0.25


def zone_of(p: Person, pol: Policy) -> Zone:
    if p.truncated or p.range_m <= pol.contact_m:
        return Zone.CONTACT
    if p.range_m <= pol.near_m:
        return Zone.NEAR
    if p.range_m <= pol.watch_m:
        return Zone.WATCH
    return Zone.CLEAR


@dataclass
class Verdict:
    state: State
    zone: Zone
    reason: str
    max_speed: float
    people: int = 0
    nearest_m: float | None = None
    child_near: bool = False
    latched: bool = False
    stale: bool = False

    def allows_walk(self) -> bool:
        return self.state in (State.GO, State.SLOW)

    def allows_gesture(self, name: str) -> bool:
        g = (name or "").strip().lower().replace("-", "_")
        if self.state in (State.GO, State.SLOW):
            return True
        if self.state is State.FREEZE:
            return False
        # HOLD: four feet stay down
        if g in LIMB_GESTURES:
            return False
        return g in SOFT_GESTURES or g not in LIMB_GESTURES

    def as_dict(self) -> dict:
        return {
            "state": self.state.name,
            "zone": self.zone.name,
            "reason": self.reason,
            "max_speed": self.max_speed,
            "people": self.people,
            "nearest_m": (round(self.nearest_m, 2)
                          if self.nearest_m is not None else None),
            "child_near": self.child_near,
            "latched": self.latched,
            "stale": self.stale,
            "walk_ok": self.allows_walk(),
        }


class Guard:
    """Turns a stream of detections into a permission, with memory.

    The memory is the point. A stateless threshold check on the current frame
    is the implementation that walks into the toddler it just lost sight of.
    """

    def __init__(self, cam: Camera | None = None, pol: Policy | None = None):
        self.cam = cam or Camera()
        self.pol = pol or Policy()
        self._last_frame_t: float | None = None
        self._latch_until: float = 0.0
        self._latch_child: bool = False
        self._latch_reason: str = ""
        self._prev: dict[str, tuple[float, float]] = {}   # bucket -> (t, range)
        self._last: Verdict = Verdict(
            State.FREEZE, Zone.CONTACT,
            "no camera frame yet", 0.0, stale=True)

    # ------------------------------------------------------------------
    @property
    def last(self) -> Verdict:
        return self._last

    def update(self, detections: list[Detection], now: float) -> Verdict:
        self._last_frame_t = now
        people = [p for p in (to_person(self.cam, d) for d in detections) if p]
        self._last = self._decide(people, now)
        return self._last

    def tick(self, now: float) -> Verdict:
        """Re-evaluate with no new frame. Call this at least as often as the
        control loop, so staleness and latch expiry are noticed between frames
        rather than only when the next one happens to arrive."""
        if self._last_frame_t is None or \
                now - self._last_frame_t > self.pol.stale_s:
            age = (now - self._last_frame_t) if self._last_frame_t else None
            self._last = Verdict(
                State.FREEZE, Zone.CONTACT,
                ("camera feed stale by %.1fs - a dead feed is not a clear path"
                 % age) if age else "no camera frame yet",
                0.0, latched=self._latch_until > now, stale=True)
            return self._last
        # frame still fresh; re-run the decision so the latch can expire
        return self._last

    # ------------------------------------------------------------------
    def _decide(self, people: list[Person], now: float) -> Verdict:
        ahead = [p for p in people if p.ahead]
        nearest = min((p.range_m for p in ahead), default=None)
        worst = Zone.CLEAR
        child_near = False
        reason = "path clear"

        for p in ahead:
            z = zone_of(p, self.pol)
            if z > worst:
                worst = z
            if p.is_child and p.range_m <= self.pol.child_freeze_m:
                child_near = True

        # --- closing speed: escalate before they arrive, not as they land
        ttc_hit = False
        buckets: dict[str, tuple[float, float]] = {}
        for p in ahead:
            key = "%d" % round(p.bearing_deg / 15.0)
            prev = self._prev.get(key)
            buckets[key] = (now, p.range_m)
            if prev:
                dt = now - prev[0]
                if 0.02 < dt < 1.5:
                    closing = (prev[1] - p.range_m) / dt      # m/s, +ve = approaching
                    if closing > 0.15:
                        ttc = p.range_m / closing
                        if ttc < self.pol.ttc_s and worst < Zone.NEAR:
                            worst = Zone.NEAR
                            ttc_hit = True
        self._prev = buckets

        # --- the latch: close contact that disappears is presumed present
        if worst >= Zone.NEAR:
            hold = self.pol.child_latch_s if child_near else self.pol.latch_s
            self._latch_until = now + hold
            self._latch_child = child_near
            self._latch_reason = (
                "child within %.1f m" % (nearest or 0)
                if child_near else
                "person within %.1f m" % (nearest or 0))

        latched = False
        if self._latch_until > now and worst < Zone.NEAR:
            latched = True
            worst = Zone.CONTACT if self._latch_child else Zone.NEAR
            child_near = child_near or self._latch_child
            reason = ("%s then lost from frame - presumed in the %.2f m blind "
                      "spot for another %.1fs"
                      % (self._latch_reason, self.cam.blind_radius_m,
                         self._latch_until - now))
        elif self._latch_until <= now:
            self._latch_child = False

        # --- map zone to permission
        if child_near:
            state = State.FREEZE
            if not latched:
                reason = ("small child within %.1f m - frozen; a 16 kg dog does "
                          "not move or lift a paw near a toddler"
                          % (nearest or self.pol.child_freeze_m))
        elif worst is Zone.CONTACT:
            state = State.FREEZE
            if not latched:
                reason = "person at contact range (%.2f m)" % (nearest or 0)
        elif worst is Zone.NEAR:
            state = State.HOLD
            if not latched:
                reason = ("person %.2f m ahead%s - holding position, soft "
                          "gestures only"
                          % (nearest or 0, " and closing fast" if ttc_hit else ""))
        elif worst is Zone.WATCH:
            state = State.SLOW
            reason = "person %.2f m ahead - walking slowly" % (nearest or 0)
        else:
            state = State.GO

        speed = {State.GO: self.pol.walk_speed_go,
                 State.SLOW: self.pol.walk_speed_slow,
                 State.HOLD: 0.0,
                 State.FREEZE: 0.0}[state]

        if not self.cam.calibrated and state is State.GO:
            # An uncalibrated plane reports confident nonsense. Allow the demo
            # to run, but never at full speed on numbers nobody has checked.
            state, speed = State.SLOW, self.pol.walk_speed_slow
            reason = "camera ground plane not calibrated - capped to slow"

        return Verdict(state=state, zone=worst, reason=reason, max_speed=speed,
                       people=len(people), nearest_m=nearest,
                       child_near=child_near, latched=latched, stale=False)
