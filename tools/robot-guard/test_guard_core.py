"""Tests for the safety logic. No camera, no torch, no robot - stdlib only.

Run:  python3 tools/robot-guard/test_guard_core.py

The scenarios that matter are the ones where the obvious implementation gets
it wrong: the child who walks into the blind spot, the feed that dies, the box
clipped by the bottom of the frame.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from guard_core import (                                      # noqa: E402
    Camera, Detection, Guard, Policy, State, Zone, to_person)


CAM = Camera(calibrated=True)       # spec geometry, pretend-calibrated


def box_at(cam: Camera, range_m: float, real_h: float, bearing_deg: float = 0.0):
    """Synthesise the box a person of `real_h` at `range_m` would produce.

    Inverse of the projection, so the tests exercise real geometry rather than
    hand-picked pixel numbers.
    """
    import math
    below = math.atan(cam.height_m / range_m) - math.radians(cam.tilt_deg)
    foot_y = cam.height / 2 + math.tan(below) * cam.fy
    px_h = real_h * cam.fy / range_m
    cx = (bearing_deg / cam.hfov_deg + 0.5) * cam.width
    return Detection(x1=cx - px_h / 6, y1=foot_y - px_h,
                     x2=cx + px_h / 6, y2=foot_y, conf=0.9)


class TestGeometry(unittest.TestCase):

    def test_blind_spot_matches_the_spec(self):
        # 0.40 m up, VFOV 70, 12 deg down -> nothing on the floor inside ~0.37 m
        self.assertAlmostEqual(CAM.blind_radius_m, 0.372, places=2)
        # looking level, it is worse
        self.assertAlmostEqual(Camera(tilt_deg=0).blind_radius_m, 0.571, places=2)

    def test_round_trip_range(self):
        for r in (1.0, 2.0, 3.0, 5.0, 8.0):
            d = box_at(CAM, r, 1.70)
            p = to_person(CAM, d)
            self.assertIsNotNone(p)
            self.assertAlmostEqual(p.range_m, r, places=1,
                                   msg="range round-trip failed at %.1f m" % r)
            self.assertAlmostEqual(p.height_m, 1.70, places=1)

    def test_above_horizon_is_refused_not_guessed(self):
        # a 'person' whose feet are above the horizon is not standing on our
        # floor. Returning None beats returning a confident wrong number.
        self.assertIsNone(CAM.ground_range_m(10.0))
        self.assertIsNone(to_person(CAM, Detection(600, 0, 680, 10)))

    def test_child_height_classification(self):
        toddler = to_person(CAM, box_at(CAM, 2.0, 0.85))
        adult = to_person(CAM, box_at(CAM, 2.0, 1.75))
        self.assertTrue(toddler.is_child)
        self.assertFalse(adult.is_child)

    def test_toddler_is_big_enough_to_detect_at_range(self):
        # 0.85 m tall at 6 m is still ~73 px - well above any detector's floor
        d = box_at(CAM, 6.0, 0.85)
        self.assertGreater(d.px_h, 60)


class TestZones(unittest.TestCase):

    def setUp(self):
        self.g = Guard(CAM, Policy())

    def test_clear_path_lets_it_walk(self):
        v = self.g.update([], 100.0)
        self.assertIs(v.state, State.GO)
        self.assertTrue(v.allows_walk())
        self.assertTrue(v.allows_gesture("shake"))
        self.assertTrue(v.allows_gesture("greet"))

    def test_distant_person_slows_but_does_not_stop(self):
        v = self.g.update([box_at(CAM, 3.2, 1.75)], 100.0)
        self.assertIs(v.state, State.SLOW)
        self.assertTrue(v.allows_walk())
        self.assertLess(v.max_speed, Policy().walk_speed_go)

    def test_person_standing_by_stops_the_walk(self):
        v = self.g.update([box_at(CAM, 1.4, 1.75)], 100.0)
        self.assertIs(v.state, State.HOLD)
        self.assertFalse(v.allows_walk())
        # but it can still nod at them - inert is a bad greeter
        self.assertTrue(v.allows_gesture("nod"))
        # greet ends in shakeHand(), a raised paw
        self.assertFalse(v.allows_gesture("greet"))

    def test_person_at_contact_range_freezes_everything(self):
        v = self.g.update([box_at(CAM, 0.7, 1.75)], 100.0)
        self.assertIs(v.state, State.FREEZE)
        self.assertFalse(v.allows_walk())
        self.assertFalse(v.allows_gesture("nod"))

    def test_someone_off_to_the_side_is_not_in_the_way(self):
        v = self.g.update([box_at(CAM, 1.2, 1.75, bearing_deg=55)], 100.0)
        self.assertIs(v.state, State.GO)

    def test_uncalibrated_camera_never_runs_at_full_speed(self):
        g = Guard(Camera(calibrated=False), Policy())
        v = g.update([], 100.0)
        self.assertIs(v.state, State.SLOW)
        self.assertIn("not calibrated", v.reason)


class TestChild(unittest.TestCase):

    def test_toddler_within_two_metres_freezes_and_bans_paws(self):
        g = Guard(CAM, Policy())
        v = g.update([box_at(CAM, 1.8, 0.85)], 100.0)
        self.assertIs(v.state, State.FREEZE)
        self.assertTrue(v.child_near)
        self.assertFalse(v.allows_gesture("greet"))
        self.assertFalse(v.allows_gesture("nod"))     # frozen means frozen

    def test_toddler_freezes_where_an_adult_would_only_hold(self):
        a = Guard(CAM, Policy()).update([box_at(CAM, 1.8, 1.75)], 100.0)
        c = Guard(CAM, Policy()).update([box_at(CAM, 1.8, 0.85)], 100.0)
        self.assertIs(a.state, State.HOLD)
        self.assertIs(c.state, State.FREEZE)


class TestGestureClassification(unittest.TestCase):
    """Checked against the real library, agibot_d1_bridge/d1w_gestures.py."""

    def hold(self):
        return Guard(CAM, Policy()).update([box_at(CAM, 1.4, 1.75)], 100.0)

    def test_every_attitude_gesture_keeps_its_feet_down(self):
        # all eight GESTURES are net-zero attitudeControl profiles: the feet
        # never move, so all eight are safe with someone standing close
        v = self.hold()
        for g in ('nod', 'shake', 'tilt', 'bow', 'squat', 'wiggle',
                  'look_around', 'sway_circle'):
            self.assertTrue(v.allows_gesture(g),
                            "%s keeps four feet down and should be allowed" % g)

    def test_greet_and_welcome_are_blocked_because_they_shake_hands(self):
        v = self.hold()
        for g in ('greet', 'welcome', 'shake_hand'):
            self.assertFalse(v.allows_gesture(g),
                             "%s calls shakeHand() and raises a paw" % g)

    def test_shake_is_a_head_shake_not_a_handshake(self):
        # yaw +/-14 deg. Banning it was a misreading that cost a harmless gesture.
        self.assertTrue(self.hold().allows_gesture('shake'))

    def test_an_unclassified_gesture_fails_closed(self):
        v = self.hold()
        self.assertFalse(v.allows_gesture('moonwalk'),
                         "unknown gesture was allowed - the gate defaults to yes")
        self.assertFalse(v.allows_gesture(''))
        self.assertFalse(v.allows_gesture(None))

    def test_naming_is_forgiving(self):
        v = self.hold()
        self.assertTrue(v.allows_gesture('LOOK-AROUND'))
        self.assertTrue(v.allows_gesture('  nod  '))
        self.assertFalse(v.allows_gesture('Shake-Hand'))

    def test_nothing_is_allowed_while_frozen(self):
        v = Guard(CAM, Policy()).update([box_at(CAM, 0.7, 1.75)], 100.0)
        self.assertIs(v.state, State.FREEZE)
        for g in ('nod', 'bow', 'greet', 'anything'):
            self.assertFalse(v.allows_gesture(g))

    def test_everything_is_allowed_with_a_clear_path(self):
        v = Guard(CAM, Policy()).update([], 100.0)
        for g in ('nod', 'greet', 'welcome', 'sway_circle', 'moonwalk'):
            self.assertTrue(v.allows_gesture(g))


class TestBlindSpotLatch(unittest.TestCase):
    """The scenario the naive version gets lethally wrong."""

    def test_adult_lost_at_close_range_is_presumed_present(self):
        g = Guard(CAM, Policy())
        g.update([box_at(CAM, 1.2, 1.75)], 100.0)       # someone right there
        v = g.update([], 100.3)                          # ...and gone from frame
        self.assertTrue(v.latched)
        self.assertFalse(v.allows_walk(),
                         "walked off the instant it lost sight of them")
        self.assertIn("blind spot", v.reason)

    def test_the_latch_does_expire(self):
        g = Guard(CAM, Policy(latch_s=3.0))
        g.update([box_at(CAM, 1.2, 1.75)], 100.0)
        self.assertFalse(g.update([], 102.0).allows_walk())   # still held
        v = g.update([], 103.5)                               # 3.5 s later
        self.assertFalse(v.latched)
        self.assertIs(v.state, State.GO)

    def test_toddler_vanishing_holds_longer_and_harder(self):
        g = Guard(CAM, Policy(latch_s=3.0, child_latch_s=5.0))
        g.update([box_at(CAM, 1.5, 0.85)], 100.0)
        # at 4 s an adult latch would have expired; the child latch has not
        v = g.update([], 104.0)
        self.assertTrue(v.latched)
        self.assertIs(v.state, State.FREEZE)
        self.assertFalse(v.allows_gesture("shake"),
                         "offered a paw into the blind spot a toddler is in")
        self.assertIs(g.update([], 105.5).state, State.GO)

    def test_a_toddler_who_walks_in_while_shaking_hands(self):
        """End to end, the user's actual scenario."""
        g = Guard(CAM, Policy())
        t = 100.0
        # mid-gesture, nobody about
        self.assertTrue(g.update([], t).allows_gesture("shake"))
        # toddler appears at 3 m and closes
        for r in (3.0, 2.4, 1.9, 1.3):
            t += 0.4
            v = g.update([box_at(CAM, r, 0.85)], t)
        self.assertIs(v.state, State.FREEZE)
        self.assertFalse(v.allows_gesture("shake"))
        # now under the camera, invisible - this is where it must NOT relax
        t += 0.3
        v = g.update([], t)
        self.assertFalse(v.allows_gesture("greet"))
        self.assertFalse(v.allows_walk())


class TestClosingSpeed(unittest.TestCase):

    def test_someone_running_in_escalates_early(self):
        g = Guard(CAM, Policy())
        g.update([box_at(CAM, 3.8, 1.75)], 100.0)
        v = g.update([box_at(CAM, 2.6, 1.75)], 100.4)   # 3 m/s closing
        self.assertGreaterEqual(v.zone, Zone.NEAR)
        self.assertFalse(v.allows_walk())

    def test_someone_walking_away_does_not(self):
        g = Guard(CAM, Policy())
        g.update([box_at(CAM, 2.6, 1.75)], 100.0)
        v = g.update([box_at(CAM, 3.8, 1.75)], 100.4)
        self.assertIs(v.state, State.SLOW)


class TestFailSafe(unittest.TestCase):

    def test_no_frame_yet_is_frozen_not_clear(self):
        g = Guard(CAM, Policy())
        self.assertIs(g.last.state, State.FREEZE)
        self.assertTrue(g.last.stale)

    def test_stale_feed_freezes(self):
        g = Guard(CAM, Policy(stale_s=0.5))
        g.update([], 100.0)
        self.assertIs(g.tick(100.2).state, State.GO)      # still fresh
        v = g.tick(101.0)                                  # a second of silence
        self.assertIs(v.state, State.FREEZE)
        self.assertTrue(v.stale)
        self.assertIn("not a clear path", v.reason)

    def test_box_clipped_by_the_frame_bottom_is_treated_as_contact(self):
        # feet out of shot = nearer than anything we can measure
        d = Detection(x1=500, y1=100, x2=800, y2=CAM.height)
        v = Guard(CAM, Policy()).update([d], 100.0)
        self.assertIs(v.state, State.FREEZE)
        self.assertIs(v.zone, Zone.CONTACT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
