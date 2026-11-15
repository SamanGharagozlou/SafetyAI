"""Tests for the visibility ring. No camera, no robot."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from awareness import FrameQuality, VisibilityRing, wrap180   # noqa: E402


class P:
    """Stand-in for guard_core.Person."""
    def __init__(self, bearing_deg, range_m, width_deg=12.0):
        self.bearing_deg, self.range_m, self.width_deg = \
            bearing_deg, range_m, width_deg


GOOD = FrameQuality()


class TestFrameQuality(unittest.TestCase):

    def test_a_good_frame_is_usable(self):
        self.assertTrue(GOOD.usable)
        self.assertIsNone(GOOD.problem)

    def test_backlit_glass_is_caught(self):
        q = FrameQuality(mean_luma=244)
        self.assertFalse(q.usable)
        self.assertIn("blown out", q.problem)

    def test_dark_blurred_and_flat_frames_are_caught(self):
        self.assertIn("too dark", FrameQuality(mean_luma=12).problem)
        self.assertIn("blurred", FrameQuality(blur_var=11).problem)
        self.assertIn("contrast", FrameQuality(rms_contrast=4).problem)

    def test_being_stood_in_front_of_is_caught(self):
        q = FrameQuality(occluded_frac=0.8)
        self.assertFalse(q.usable)
        self.assertIn("body at close range", q.problem)


class TestRing(unittest.TestCase):

    def setUp(self):
        self.r = VisibilityRing(stale_after_s=3.0)

    def test_a_fresh_look_lets_it_commit(self):
        self.r.observe(0.0, GOOD, [], now=100.0)
        ok, why = self.r.can_commit_to(0.0, 100.1, 1.5)
        self.assertTrue(ok, why)

    def test_knowledge_goes_stale(self):
        self.r.observe(0.0, GOOD, [], now=100.0)
        self.assertTrue(self.r.can_commit_to(0.0, 102.0, 1.5)[0])
        ok, why = self.r.can_commit_to(0.0, 105.0, 1.5)
        self.assertFalse(ok)
        self.assertIn("ago", why)

    def test_it_never_commits_to_a_direction_it_has_not_looked(self):
        self.r.observe(0.0, GOOD, [], now=100.0)
        ok, why = self.r.can_commit_to(170.0, 100.1, 1.5)   # behind
        self.assertFalse(ok)
        self.assertIn("never looked", why)

    def test_only_sectors_inside_the_lens_are_refreshed(self):
        # HFOV 122 -> +/-61 deg. 80 deg off axis was not seen.
        self.r.observe(0.0, GOOD, [], now=100.0)
        self.assertFalse(self.r.is_stale(50.0, 100.1))
        self.assertTrue(self.r.is_stale(80.0, 100.1))

    def test_a_bad_frame_refreshes_nothing(self):
        """The rule the whole module rests on."""
        bad = FrameQuality(mean_luma=250)
        self.r.observe(0.0, bad, [], now=100.0)
        self.assertTrue(self.r.is_stale(0.0, 100.1),
                        "a blown-out frame counted as having looked")
        ok, why = self.r.can_commit_to(0.0, 100.1, 1.5)
        self.assertFalse(ok)
        self.assertIn("unusable", why)

    def test_a_good_frame_after_a_bad_one_recovers(self):
        self.r.observe(0.0, FrameQuality(mean_luma=250), [], now=100.0)
        self.r.observe(0.0, GOOD, [], now=100.5)
        self.assertTrue(self.r.can_commit_to(0.0, 100.6, 1.5)[0])

    def test_a_body_blocks_the_view_behind_it(self):
        self.r.observe(0.0, GOOD, [P(bearing_deg=0, range_m=1.5)], now=100.0)
        ok, why = self.r.can_commit_to(0.0, 100.1, 3.0)
        self.assertFalse(ok)
        self.assertIn("blocked at 1.5", why)
        # ...but not the view either side of them
        self.assertTrue(self.r.can_commit_to(40.0, 100.1, 3.0)[0])


class TestScanPlanning(unittest.TestCase):

    def setUp(self):
        self.r = VisibilityRing(stale_after_s=3.0)

    def test_nothing_seen_yet_means_look_where_you_are_going(self):
        self.assertAlmostEqual(self.r.scan_target(0.0, 100.0), 0.0, places=1)

    def test_a_fresh_view_everywhere_needs_no_scan(self):
        for yaw in range(-180, 180, 40):
            self.r.observe(float(yaw), GOOD, [], now=100.0)
        self.assertIsNone(self.r.scan_target(0.0, 100.1))

    def test_an_unusable_frame_always_demands_a_look(self):
        for yaw in range(-180, 180, 40):
            self.r.observe(float(yaw), GOOD, [], now=100.0)
        self.r.observe(0.0, FrameQuality(mean_luma=5), [], now=100.2)
        self.assertIsNotNone(self.r.scan_target(0.0, 100.3))

    def test_it_looks_at_the_stale_flank_not_the_fresh_centre(self):
        self.r.observe(0.0, GOOD, [], now=100.0)        # ahead is fresh
        t = self.r.scan_target(0.0, 100.5, arc_deg=200.0)
        self.assertIsNotNone(t)
        self.assertGreater(abs(wrap180(t)), 50.0,
                           "re-scanned a sector it had just looked at")

    def test_the_sweep_skips_what_was_just_seen(self):
        self.r.observe(0.0, GOOD, [], now=100.0)
        plan = self.r.sweep_plan(0.0, 100.2, arc_deg=240.0)
        self.assertTrue(all(abs(wrap180(b)) > 50 for b in plan),
                        "sweep included the sector already in view: %s" % plan)

    def test_the_sweep_does_not_aim_twice_at_one_lens_width(self):
        plan = self.r.sweep_plan(0.0, 100.0, arc_deg=240.0)
        for i, a in enumerate(plan):
            for b in plan[i + 1:]:
                self.assertGreater(abs(wrap180(a - b)), 60.0,
                                   "two aims overlap: %s" % plan)

    def test_turning_cost_is_why_the_plan_is_short(self):
        # a 240 deg arc on a 122 deg lens needs very few aims
        plan = self.r.sweep_plan(0.0, 100.0, arc_deg=240.0)
        self.assertLessEqual(len(plan), 4, plan)
        self.assertGreaterEqual(len(plan), 2, plan)


if __name__ == "__main__":
    unittest.main(verbosity=2)
