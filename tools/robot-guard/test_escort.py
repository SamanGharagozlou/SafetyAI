"""Tests for the follow-me escort. No camera, no robot."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from awareness import FrameQuality, VisibilityRing            # noqa: E402
from escort_core import (Candidate, Escort, EscortPolicy,      # noqa: E402
                         Phase, cosine)

GOOD = FrameQuality()
RED = (0.9, 0.1, 0.1)
BLUE = (0.1, 0.1, 0.9)


def ring_seeing_everything(now=100.0):
    r = VisibilityRing(stale_after_s=30.0)
    for yaw in range(-180, 180, 40):
        r.observe(float(yaw), GOOD, [], now=now)
    return r


def drive(e, now, guard=True, limit=12):
    """Run the machine until it asks for something the test must answer."""
    for _ in range(limit):
        a = e.next_action(now, guard_allows_walk=guard)
        if a.kind == "turn":
            e.turned(a.bearing_deg)
            continue
        return a
    raise AssertionError("escort span without settling")


class TestAppearance(unittest.TestCase):

    def test_cosine_basics(self):
        self.assertAlmostEqual(cosine(RED, RED), 1.0, places=5)
        self.assertLess(cosine(RED, BLUE), 0.3)
        self.assertEqual(cosine((), RED), 0.0)
        self.assertEqual(cosine((1.0, 2.0), (1.0,)), 0.0)


class TestHappyPath(unittest.TestCase):

    def test_it_walks_legs_and_checks_between_them(self):
        e = Escort(ring_seeing_everything(), EscortPolicy(leg_cm=150))
        e.start(450, "the restaurant",
                follower=Candidate(range_m=1.6, bearing_deg=0, appearance=RED),
                now=100.0)
        a = drive(e, 100.0)
        self.assertEqual(a.kind, "walk")
        self.assertAlmostEqual(a.distance_cm, 150.0)

        e.walked(150, 104.0)
        self.assertIs(e.phase, Phase.LOOKING_BACK)

        # look back, guest is there
        drive(e, 104.0)
        a = e.saw([Candidate(range_m=1.8, bearing_deg=5, appearance=RED)], 104.5)
        self.assertEqual(a.kind, "turn")
        self.assertIs(e.phase, Phase.CLEARING)
        self.assertEqual(e.follower.range_m, 1.8)

    def test_it_verifies_once_more_on_arrival(self):
        e = Escort(ring_seeing_everything(), EscortPolicy(leg_cm=150))
        e.start(150, "the pool",
                follower=Candidate(range_m=1.5, bearing_deg=0, appearance=RED),
                now=100.0)
        drive(e, 100.0)
        e.walked(150, 104.0)
        self.assertIs(e.phase, Phase.LOOKING_BACK,
                      "declared arrival without checking the guest came too")
        drive(e, 104.0)
        a = e.saw([Candidate(range_m=1.7, bearing_deg=0, appearance=RED)], 104.5)
        self.assertEqual(a.kind, "done")
        self.assertIs(e.phase, Phase.ARRIVED)


class TestLosingTheGuest(unittest.TestCase):

    def test_an_empty_look_back_makes_it_ask_and_wait(self):
        e = Escort(ring_seeing_everything(), EscortPolicy(leg_cm=150))
        e.start(450, "the spa",
                follower=Candidate(range_m=1.6, bearing_deg=0, appearance=RED),
                now=100.0)
        drive(e, 100.0)
        e.walked(150, 104.0)
        drive(e, 104.0)
        a = e.saw([], 104.5)
        self.assertEqual(a.kind, "say")
        self.assertIn("Still with me", a.text)
        self.assertIs(e.phase, Phase.WAITING)

    def test_it_stops_rather_than_arriving_alone(self):
        e = Escort(ring_seeing_everything(),
                   EscortPolicy(leg_cm=150, max_missed_checks=2, wait_s=5))
        e.start(450, "the spa",
                follower=Candidate(range_m=1.6, bearing_deg=0, appearance=RED),
                now=100.0)
        drive(e, 100.0)
        e.walked(150, 104.0)
        drive(e, 104.0)
        e.saw([], 104.5)                       # miss 1 -> wait
        drive(e, 110.0)                        # wait expires, looks again
        a = e.saw([], 110.5)                   # miss 2 -> give up
        self.assertEqual(a.kind, "say")
        self.assertIn("lost you", a.text)
        self.assertIs(e.phase, Phase.ABORTED)
        self.assertEqual(e.next_action(111.0).kind, "done")

    def test_it_does_not_walk_on_after_aborting(self):
        e = Escort(ring_seeing_everything(), EscortPolicy(max_missed_checks=1))
        e.start(450, "spa", follower=Candidate(1.6, 0, appearance=RED), now=100.0)
        drive(e, 100.0)
        e.walked(150, 104.0)
        drive(e, 104.0)
        e.saw([], 104.5)
        for t in (105.0, 120.0, 200.0):
            self.assertEqual(e.next_action(t).kind, "done")


class TestFollowingProperly(unittest.TestCase):

    def test_someone_dawdling_makes_it_wait_not_march_on(self):
        e = Escort(ring_seeing_everything(), EscortPolicy(lagging_m=3.0))
        e.start(600, "spa", follower=Candidate(1.5, 0, appearance=RED), now=100.0)
        drive(e, 100.0)
        e.walked(150, 104.0)
        drive(e, 104.0)
        a = e.saw([Candidate(range_m=4.2, bearing_deg=0, appearance=RED)], 104.5)
        self.assertEqual(a.kind, "say")
        self.assertIn("wait", a.text.lower())
        self.assertIs(e.phase, Phase.WAITING)

    def test_a_steadily_growing_gap_is_recognised_as_falling_behind(self):
        e = Escort(ring_seeing_everything(),
                   EscortPolicy(leg_cm=150, lagging_m=9.0))   # distance gate off
        # The sighting taken at start() - while the guest is still in FRONT of
        # the robot - is a real data point, so the trend needs only two
        # look-backs after it, not three.
        e.start(900, "spa", follower=Candidate(1.2, 0, appearance=RED), now=100.0)
        self.assertEqual(e.follower.trend, "unknown")

        t = 100.0
        drive(e, t); e.walked(150, t); drive(e, t)
        e.saw([Candidate(range_m=1.9, bearing_deg=0, appearance=RED)], t)
        self.assertEqual(e.follower.trend, "unknown")   # two points says nothing
        self.assertIs(e.phase, Phase.CLEARING)          # so it carries on

        t += 5.0
        drive(e, t); e.walked(150, t); drive(e, t)
        a = e.saw([Candidate(range_m=2.6, bearing_deg=0, appearance=RED)], t)
        self.assertEqual(e.follower.trend, "falling_behind")
        self.assertIs(e.phase, Phase.WAITING)
        self.assertEqual(a.kind, "say")

    def test_a_steady_gap_is_keeping_up(self):
        e = Escort(ring_seeing_everything(), EscortPolicy(leg_cm=150))
        e.start(900, "spa", follower=Candidate(1.5, 0, appearance=RED), now=100.0)
        t = 100.0
        for r in (1.6, 1.5, 1.7):
            drive(e, t); e.walked(150, t); drive(e, t)
            e.saw([Candidate(range_m=r, bearing_deg=0, appearance=RED)], t)
            t += 5.0
        self.assertEqual(e.follower.trend, "keeping_up")
        self.assertIs(e.phase, Phase.CLEARING)


class TestIdentity(unittest.TestCase):

    def test_it_prefers_the_guest_over_a_passer_by(self):
        e = Escort(ring_seeing_everything(), EscortPolicy())
        e.start(450, "spa", follower=Candidate(1.6, 0, appearance=RED), now=100.0)
        drive(e, 100.0); e.walked(150, 104.0); drive(e, 104.0)
        e.saw([Candidate(range_m=1.7, bearing_deg=-30, appearance=BLUE),
               Candidate(range_m=2.1, bearing_deg=10, appearance=RED)], 104.5)
        self.assertAlmostEqual(e.follower.range_m, 2.1,
                               msg="locked onto the wrong person")

    def test_someone_far_too_close_is_not_a_follower(self):
        e = Escort(ring_seeing_everything(), EscortPolicy(follow_min_m=0.8))
        e.start(450, "spa", follower=Candidate(1.6, 0, appearance=RED), now=100.0)
        drive(e, 100.0); e.walked(150, 104.0); drive(e, 104.0)
        a = e.saw([Candidate(range_m=0.4, bearing_deg=0, appearance=RED)], 104.5)
        self.assertEqual(a.kind, "say")           # treated as no follower
        self.assertIs(e.phase, Phase.WAITING)

    def test_someone_across_the_car_park_is_not_a_follower(self):
        e = Escort(ring_seeing_everything(), EscortPolicy(follow_max_m=5.0))
        e.start(450, "spa", follower=Candidate(1.6, 0, appearance=RED), now=100.0)
        drive(e, 100.0); e.walked(150, 104.0); drive(e, 104.0)
        a = e.saw([Candidate(range_m=9.0, bearing_deg=0, appearance=RED)], 104.5)
        self.assertEqual(a.kind, "say")
        self.assertIs(e.phase, Phase.WAITING)


class TestVisibilityBeforeCommitting(unittest.TestCase):

    def test_it_looks_before_walking_into_a_direction_it_cannot_vouch_for(self):
        r = VisibilityRing(stale_after_s=3.0)       # nothing observed at all
        e = Escort(r, EscortPolicy())
        e.start(450, "spa", follower=Candidate(1.6, 0, appearance=RED), now=100.0)
        a = e.next_action(100.0)
        self.assertEqual(a.kind, "turn", "walked without looking first")
        self.assertIn("no usable view", e.note)

    def test_a_blown_out_frame_stops_it_committing(self):
        r = ring_seeing_everything(now=100.0)
        r.observe(0.0, FrameQuality(mean_luma=248), [], now=100.5)
        e = Escort(r, EscortPolicy())
        e.start(450, "spa", follower=Candidate(1.6, 0, appearance=RED),
                now=100.6)
        a = e.next_action(100.6)
        self.assertEqual(a.kind, "turn")
        self.assertIn("unusable", e.note)

    def test_the_people_guard_outranks_the_escort(self):
        e = Escort(ring_seeing_everything(), EscortPolicy())
        e.start(450, "spa", follower=Candidate(1.6, 0, appearance=RED), now=100.0)
        a = e.next_action(100.0, guard_allows_walk=False,
                          guard_reason="child within 1.8 m")
        self.assertEqual(a.kind, "wait")
        self.assertIn("child", e.note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
