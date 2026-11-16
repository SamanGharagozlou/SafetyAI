"""
Does the robot currently have visibility, and if not, where should it look?

Pure stdlib, so it is testable with the robot flat.

THE COST OF LOOKING, which drives every decision in here:

The camera is BODY-MOUNTED. There is no pan-tilt head - the spec lists no head
servo and the SDK has no pan API - so "look left" means "turn the robot left".
Two ways, and neither is cheap:

    attitudeControl(roll, pitch, yaw, height)   twist in place, yaw +/-0.5 rad/s
        The docs call it 原地扭动, twist-in-place. It does not relocate the feet,
        so the available yaw is a body twist of a couple of dozen degrees - a
        glance, not a look.

    move(vx, vy, yaw_rate)                      turn in place, yaw +/-3.0 rad/s
        A real heading change. At a sane 1.0 rad/s near people: 60 deg takes
        ~1.0 s, 180 deg takes ~3.1 s - and it has to be undone afterwards.

So a left-and-right sweep costs ~3 s of turning and a look behind ~6 s round
trip. That is far too expensive to spend every metre on principle. It is worth
spending the moment the robot cannot see, which is what this module decides.

WHAT "NO VISIBILITY" ACTUALLY MEANS, in signals we can measure:

    frame unusable   blown out, too dark, too blurred, too low contrast.
                     The entrance at Leukerbad is glass. Backlit glass
                     saturates a 122 deg lens, and a saturated frame is not a
                     view of an empty forecourt.
    occluded         somebody is standing close enough to fill the frame.
                     Nothing behind them is visible at any range.
    stale            we have not pointed the camera that way recently enough
                     to believe anything about it. People move.

THE RULE THAT MAKES IT HONEST: a bad frame NEVER refreshes the map.

If a blown-out frame counted as "I looked and it was clear", the robot would
talk itself into confidence it had not earned, and the staler its knowledge got
the more certain it would become. Unusable frames mark the direction UNKNOWN
and demand a fresh look. This is the same failure this stack already shipped
once - an endpoint answering 200 with nulls while the robot lay on the floor -
and it is the same fix: absence of information must never read as good news.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def wrap180(deg: float) -> float:
    """Normalise to [-180, 180)."""
    return (deg + 180.0) % 360.0 - 180.0


def wrap360(deg: float) -> float:
    return deg % 360.0


# --------------------------------------------------------------------------
# is this frame worth believing?
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FrameQuality:
    """Cheap whole-frame statistics. Computed in guard.py where OpenCV lives;
    kept as plain numbers here so the decision logic stays testable.

    The thresholds are STARTING POINTS from the usual image-quality ranges, not
    values measured at this venue. They must be tuned against the real entrance
    in the real light before anyone relies on them - which is why every one of
    them is a constructor argument.
    """
    mean_luma: float = 128.0      # 0-255
    rms_contrast: float = 50.0    # stddev of luma
    blur_var: float = 300.0       # variance of Laplacian; low = soft/motion-blurred
    occluded_frac: float = 0.0    # fraction of frame area covered by near bodies

    dark_below: float = 25.0
    bright_above: float = 230.0
    contrast_below: float = 12.0
    blur_below: float = 40.0
    occluded_above: float = 0.50

    @property
    def problem(self) -> str | None:
        if self.mean_luma < self.dark_below:
            return "too dark (luma %.0f)" % self.mean_luma
        if self.mean_luma > self.bright_above:
            return "blown out (luma %.0f) - backlit glass does this" % self.mean_luma
        if self.rms_contrast < self.contrast_below:
            return "almost no contrast (rms %.1f)" % self.rms_contrast
        if self.blur_var < self.blur_below:
            return "blurred (laplacian var %.0f)" % self.blur_var
        if self.occluded_frac > self.occluded_above:
            return ("%.0f%% of the frame is a body at close range"
                    % (self.occluded_frac * 100))
        return None

    @property
    def usable(self) -> bool:
        return self.problem is None


# --------------------------------------------------------------------------
# what do we know about each direction, and how old is it?
# --------------------------------------------------------------------------

@dataclass
class Sector:
    last_seen_t: float | None = None   # when a USABLE frame last covered this
    clear_to_m: float = 0.0            # furthest we could see in it
    blocked_at_m: float | None = None  # a body stands here, nothing beyond it


class VisibilityRing:
    """A ring of bearing sectors with decaying freshness.

    Not a map of the place - it holds no geometry and survives no movement. It
    answers only "how recently did I look that way, and what did I see". For a
    greeter working a few metres of pavement that is the useful question;
    building a metric map to answer it would be a research project (monocular,
    122 deg lens, no wheel odometry) for no extra benefit.

    Bearings are in the WORLD frame the controller is dead-reckoning: the robot
    does not publish its heading. `getRPY()` exists in the SDK but the bridge's
    /status does not forward it, so the controller integrates the turns it
    issued itself. Over the few seconds of a scan that is accurate enough; over
    minutes it drifts, and the fix is to expose RPY from the agent.
    """

    def __init__(self, sectors: int = 24, hfov_deg: float = 122.0,
                 stale_after_s: float = 3.0, want_clear_m: float = 3.0):
        self.n = sectors
        self.width = 360.0 / sectors
        self.hfov = hfov_deg
        self.stale_after_s = stale_after_s
        self.want_clear_m = want_clear_m
        self.sectors = [Sector() for _ in range(sectors)]
        self.last_quality: FrameQuality | None = None
        self.unusable_since: float | None = None

    # ------------------------------------------------------------------
    def index(self, bearing_deg: float) -> int:
        return int(wrap360(bearing_deg) // self.width) % self.n

    def centre(self, i: int) -> float:
        return wrap180(i * self.width + self.width / 2)

    # ------------------------------------------------------------------
    def observe(self, body_yaw_deg: float, quality: FrameQuality,
                people: list, now: float, horizon_m: float = 8.0) -> None:
        """Fold one frame into the ring.

        `people` are objects with .bearing_deg (relative to the robot),
        .range_m and optionally .width_deg - i.e. guard_core.Person.
        """
        self.last_quality = quality

        if not quality.usable:
            # Mark the attempt, refresh NOTHING. An unusable frame is not
            # evidence of an empty forecourt.
            if self.unusable_since is None:
                self.unusable_since = now
            return
        self.unusable_since = None

        half = self.hfov / 2
        # Which sectors did this frame actually cover?
        covered = [i for i in range(self.n)
                   if abs(wrap180(self.centre(i) - body_yaw_deg)) <= half]

        # Bodies block everything behind them.
        blocked: dict[int, float] = {}
        for p in people:
            w = getattr(p, "width_deg", 10.0)
            lo, hi = p.bearing_deg - w / 2, p.bearing_deg + w / 2
            b = lo
            while b <= hi:
                i = self.index(body_yaw_deg + b)
                prev = blocked.get(i)
                blocked[i] = p.range_m if prev is None else min(prev, p.range_m)
                b += self.width / 2

        for i in covered:
            s = self.sectors[i]
            s.last_seen_t = now
            if i in blocked:
                s.blocked_at_m = blocked[i]
                s.clear_to_m = blocked[i]
            else:
                s.blocked_at_m = None
                s.clear_to_m = horizon_m

    # ------------------------------------------------------------------
    def age(self, bearing_deg: float, now: float) -> float | None:
        s = self.sectors[self.index(bearing_deg)]
        return None if s.last_seen_t is None else now - s.last_seen_t

    def is_stale(self, bearing_deg: float, now: float) -> bool:
        a = self.age(bearing_deg, now)
        return a is None or a > self.stale_after_s

    def clear_ahead_m(self, bearing_deg: float) -> float:
        return self.sectors[self.index(bearing_deg)].clear_to_m

    def can_commit_to(self, bearing_deg: float, now: float,
                      distance_m: float) -> tuple[bool, str]:
        """Is it reasonable to walk `distance_m` along `bearing_deg` now?"""
        if self.unusable_since is not None:
            q = self.last_quality
            return False, ("camera unusable: %s"
                           % (q.problem if q else "no frame"))
        a = self.age(bearing_deg, now)
        if a is None:
            return False, ("never looked %.0f deg" % wrap180(bearing_deg))
        if a > self.stale_after_s:
            return False, ("last looked %.0f deg %.1fs ago" %
                           (wrap180(bearing_deg), a))
        s = self.sectors[self.index(bearing_deg)]
        if s.blocked_at_m is not None and s.blocked_at_m < distance_m:
            return False, ("view %.0f deg blocked at %.1f m, short of the %.1f m leg"
                           % (wrap180(bearing_deg), s.blocked_at_m, distance_m))
        if s.clear_to_m < min(distance_m, self.want_clear_m):
            return False, ("can only see %.1f m along %.0f deg"
                           % (s.clear_to_m, wrap180(bearing_deg)))
        return True, "visibility along %.0f deg is %.1fs old, clear to %.1f m" % (
            wrap180(bearing_deg), a, s.clear_to_m)

    # ------------------------------------------------------------------
    def scan_target(self, heading_deg: float, now: float,
                    arc_deg: float = 100.0) -> float | None:
        """Where to point the camera next, as an absolute bearing. None if the
        robot already has what it needs.

        Priority: the direction of travel first, then the stalest sector within
        `arc_deg` of it - which is where something could step out from.
        Returns the bearing the BODY should face; the caller turns and undoes it.
        """
        if self.unusable_since is not None:
            # Pointless to pick a clever bearing when the frame itself is no
            # good. Look along the heading and hope the light is better.
            return wrap180(heading_deg)

        if self.is_stale(heading_deg, now):
            return wrap180(heading_deg)

        worst_i, worst_age = None, -1.0
        for i in range(self.n):
            c = self.centre(i)
            if abs(wrap180(c - heading_deg)) > arc_deg / 2:
                continue
            s = self.sectors[i]
            a = float("inf") if s.last_seen_t is None else now - s.last_seen_t
            if a > self.stale_after_s and a > worst_age:
                worst_i, worst_age = i, a
        if worst_i is None:
            return None
        return self.centre(worst_i)

    def sweep_plan(self, heading_deg: float, now: float,
                   arc_deg: float = 100.0) -> list[float]:
        """A left-right sweep around the heading, as absolute bearings.

        Only the bearings that are actually stale - there is no point paying a
        second of turning for a sector we looked at half a second ago. Ordered
        nearest-first so the cheapest turn happens first and the robot can bail
        out of the rest if something appears.
        """
        want = []
        for i in range(self.n):
            c = self.centre(i)
            off = wrap180(c - heading_deg)
            if abs(off) > arc_deg / 2:
                continue
            if self.is_stale(c, now):
                want.append((abs(off), c))
        # one look covers HFOV, so thin the list to roughly non-overlapping aims
        want.sort()
        picked: list[float] = []
        for _, c in want:
            if all(abs(wrap180(c - p)) > self.hfov * 0.6 for p in picked):
                picked.append(c)
        return picked

    # ------------------------------------------------------------------
    def summary(self, heading_deg: float, now: float) -> dict:
        fresh = sum(1 for i in range(self.n)
                    if not self.is_stale(self.centre(i), now))
        blocked = sum(1 for s in self.sectors if s.blocked_at_m is not None)
        ok, why = self.can_commit_to(heading_deg, now, 1.0)
        return {
            "sectors": self.n,
            "fresh": fresh,
            "blocked": blocked,
            "heading_deg": round(wrap180(heading_deg), 1),
            "heading_age_s": (round(self.age(heading_deg, now), 2)
                              if self.age(heading_deg, now) is not None else None),
            "can_walk": ok,
            "why": why,
            "frame_problem": (self.last_quality.problem
                              if self.last_quality else "no frame yet"),
        }
