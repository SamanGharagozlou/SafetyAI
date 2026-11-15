"""No robot, no camera, no network. Run: python3 test_clawmetry.py"""
import json
import tempfile
import unittest
from pathlib import Path

from correlate import Correlator, Policy
from ledger import Ledger, digest
from frame import FrameGrabber
from predict import BrainJudge, CosmosJudge, kinematic_predict, verify


def _fake_brain(response):
    """A BrainJudge whose HTTP call returns `response`, so verdict parsing is
    tested without a Spark."""
    import io, json as _j
    from unittest import mock
    j = BrainJudge(brain_url="http://fake", key="k")
    ctx = mock.MagicMock()
    ctx.__enter__ = lambda s: io.BytesIO(_j.dumps(response).encode())
    ctx.__exit__ = lambda s, *a: False
    j._urlopen = ctx
    orig = j.judge

    def patched(*a, **kw):
        with mock.patch("urllib.request.urlopen", return_value=ctx):
            return orig(*a, **kw)
    j.judge = patched
    return j


def ev(kind, data=None, ts=100.0, corr="", actor="system", seq=1):
    return {"kind": kind, "data": data or {}, "ts": ts, "corr": corr,
            "actor": actor, "seq": seq}


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.p = Path(self.dir.name) / "ledger.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_chain_links_and_verifies(self):
        led = Ledger(self.p, robot_id="d1w")
        for i in range(5):
            led.append("state", actor="robot", data={"i": i})
        v = led.verify()
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["count"], 5)

    def test_tampering_a_line_is_detected(self):
        led = Ledger(self.p)
        led.append("intent", actor="agent", data={"tool": "walk_distance"})
        led.append("state", actor="robot", data={"moving": True})
        lines = self.p.read_text().splitlines()
        bad = json.loads(lines[0])
        bad["data"]["tool"] = "nod"          # rewrite history: make the walk a nod
        lines[0] = json.dumps(bad)
        self.p.write_text("\n".join(lines) + "\n")
        v = led.verify()
        self.assertFalse(v["ok"])
        self.assertEqual(v["broken_at"], 1)

    def test_deleting_a_line_is_detected(self):
        led = Ledger(self.p)
        for i in range(4):
            led.append("state", actor="robot", data={"i": i})
        lines = self.p.read_text().splitlines()
        del lines[1]                          # quietly drop the inconvenient event
        self.p.write_text("\n".join(lines) + "\n")
        self.assertFalse(led.verify()["ok"])

    def test_restart_resumes_the_same_chain(self):
        led = Ledger(self.p)
        led.append("state", actor="robot")
        again = Ledger(self.p)
        again.append("state", actor="robot")
        self.assertTrue(again.verify()["ok"])
        self.assertEqual(again.read()[-1]["seq"], 2)

    def test_unknown_kind_is_refused(self):
        led = Ledger(self.p)
        with self.assertRaises(ValueError):
            led.append("whatever", actor="system")

    def test_skew_recorded_when_source_clock_disagrees(self):
        led = Ledger(self.p)
        e = led.append("state", actor="robot", src_ts=1.0)
        self.assertIsNotNone(e["skew_s"])
        self.assertGreater(e["skew_s"], 1000)   # source clock is wrong; we keep both


class TestCorrelator(unittest.TestCase):
    def setUp(self):
        self.c = Correlator(Policy())

    def test_motion_with_no_intent_is_critical(self):
        out = self.c.feed(ev("state", {"moving": True, "speed_mps": 0.3}, ts=100))
        self.assertEqual([b["rule"] for b in out], ["unattributed_motion"])
        self.assertEqual(out[0]["severity"], "critical")

    def test_motion_explained_by_an_intent_is_silent(self):
        self.c.feed(ev("intent", {"tool": "walk_distance"}, ts=100, corr="a"))
        self.c.feed(ev("decision", {"decision": "allow"}, ts=100.1, corr="a"))
        out = self.c.feed(ev("state", {"moving": True, "speed_mps": 0.3}, ts=101))
        self.assertEqual(out, [])

    def test_intent_too_old_no_longer_explains_motion(self):
        self.c.feed(ev("intent", {"tool": "walk_distance"}, ts=100, corr="a"))
        out = self.c.feed(ev("state", {"moving": True, "speed_mps": 0.3}, ts=200))
        self.assertEqual(out[0]["rule"], "unattributed_motion")

    def test_command_the_gate_never_saw(self):
        out = self.c.feed(ev("command", {"tool": "walk_distance"}, ts=100, corr="z"))
        self.assertEqual(out[0]["rule"], "gate_bypass")

    def test_stop_is_never_flagged_as_a_bypass(self):
        self.assertEqual(self.c.feed(ev("command", {"tool": "stop_robot"}, ts=100)), [])

    def test_denied_command_executed_is_the_worst_case(self):
        self.c.feed(ev("intent", {"tool": "walk_distance"}, ts=100, corr="a"))
        self.c.feed(ev("decision", {"decision": "deny"}, ts=100.1, corr="a"))
        out = self.c.feed(ev("command", {"tool": "walk_distance"}, ts=100.2, corr="a"))
        self.assertEqual(out[0]["rule"], "denied_command_executed")
        self.assertEqual(out[0]["severity"], "critical")

    def test_speed_over_cap_is_a_breach_even_when_authorised(self):
        self.c.feed(ev("intent", {"tool": "walk_distance"}, ts=100, corr="a"))
        out = self.c.feed(ev("state", {"moving": True, "speed_mps": 0.9}, ts=100.5))
        self.assertIn("policy_breach", [b["rule"] for b in out])

    def test_moving_with_a_person_close_is_critical(self):
        self.c.feed(ev("intent", {"tool": "walk_distance"}, ts=100, corr="a"))
        out = self.c.feed(ev("state", {"moving": True, "speed_mps": 0.2,
                                       "nearest_person_m": 0.6}, ts=100.5))
        b = [x for x in out if x["rule"] == "policy_breach"][0]
        self.assertEqual(b["severity"], "critical")

    def test_a_still_robot_near_a_person_is_not_a_breach(self):
        out = self.c.feed(ev("state", {"moving": False, "speed_mps": 0.0,
                                       "nearest_person_m": 0.4}, ts=100))
        self.assertEqual(out, [])

    def test_silence_is_reported_not_ignored(self):
        self.c.feed(ev("state", {"source": "bridge", "moving": False}, ts=100))
        self.assertEqual(self.c.tick(now=100.5), [])
        out = self.c.tick(now=103)
        self.assertEqual(out[0]["rule"], "silence")
        self.assertEqual(out[0]["source"], "bridge")

    def test_silence_is_reported_once_then_clears_on_return(self):
        self.c.feed(ev("state", {"source": "bridge"}, ts=100))
        self.assertEqual(len(self.c.tick(now=103)), 1)
        self.assertEqual(self.c.tick(now=104), [])            # not re-reported
        self.c.feed(ev("state", {"source": "bridge"}, ts=105))
        self.assertEqual(len(self.c.tick(now=108)), 1)        # fresh silence, reported again

    def test_claimed_walk_that_never_moves_is_caught(self):
        # a feed that COULD show motion must exist first
        self.c.feed(ev("state", {"source": "bridge", "moving": False}, ts=99))
        self.c.feed(ev("intent", {"tool": "walk_distance"}, ts=100, corr="a"))
        self.c.feed(ev("decision", {"decision": "allow"}, ts=100.1, corr="a"))
        self.c.feed(ev("command", {"tool": "walk_distance"}, ts=100.2, corr="a"))
        out = self.c.tick(now=110)
        self.assertIn("narration_divergence", [b["rule"] for b in out])

    def test_without_a_motion_feed_it_is_unverified_not_a_lie(self):
        """A real 95cm walk was twice called a lie because the state collector
        was silently 401ing. Blindness is not evidence of dishonesty."""
        self.c.feed(ev("intent", {"tool": "walk_distance"}, ts=100, corr="a"))
        self.c.feed(ev("command", {"tool": "walk_distance"}, ts=100.1, corr="a"))
        out = self.c.feed(ev("outcome", {"tool": "walk_distance", "deviation_z": 0.1},
                             ts=112, corr="a"))
        rules = [b["rule"] for b in out]
        self.assertIn("unverified_action", rules)
        self.assertNotIn("narration_divergence", rules)
        self.assertEqual(self.c.tick(now=115), [])   # and the timer stays quiet too

    def test_a_collector_that_never_starts_is_reported(self):
        """A feed that stops is caught by silence; one that never begins used
        to be invisible, which is worse."""
        c = Correlator(Policy())
        c.expect("guard", now=1000)
        self.assertEqual(c.tick(now=1005), [])          # inside the startup grace
        out = c.tick(now=1020)                          # past it
        self.assertEqual(out[0]["rule"], "source_never_reported")
        self.assertEqual(out[0]["source"], "guard")

    def test_an_expected_source_that_does_arrive_is_not_reported(self):
        c = Correlator(Policy())
        c.expect("bridge", now=1000)
        c.feed(ev("state", {"source": "bridge", "moving": False}, ts=1000))
        self.assertEqual(c.tick(now=1000.5), [])
        # and if it later stops, that is `silence` - a feed that died - never
        # `source_never_reported`, which means it never existed. Different
        # faults, different fixes.
        self.assertEqual(c.tick(now=1020)[0]["rule"], "silence")

    def test_predicted_breach_fires_before_the_action(self):
        out = self.c.feed(ev("prediction", {"predicted_breach": True, "basis": "kinematic",
                                            "reason": "path enters pool_deck"}, ts=100, corr="a"))
        self.assertEqual(out[0]["rule"], "predicted_breach")

    def test_outcome_far_from_prediction_is_a_breach(self):
        self.c.feed(ev("intent", {"tool": "walk_distance"}, ts=100, corr="a"))
        out = self.c.feed(ev("outcome", {"deviation_z": 7.4}, ts=110, corr="a"))
        self.assertEqual(out[0]["rule"], "prediction_deviation")

    def test_outcome_close_to_prediction_is_quiet(self):
        self.c.feed(ev("intent", {"tool": "walk_distance"}, ts=100, corr="a"))
        self.c.feed(ev("decision", {"decision": "allow"}, ts=100.1, corr="a"))
        self.c.feed(ev("state", {"moving": True, "speed_mps": 0.2}, ts=101))
        self.assertEqual(self.c.feed(ev("outcome", {"deviation_z": 0.8}, ts=105, corr="a")), [])

    def test_malformed_event_does_not_blind_the_detectors(self):
        self.c.feed({"kind": "nonsense"})
        out = self.c.feed(ev("state", {"moving": True, "speed_mps": 0.3}, ts=100))
        self.assertEqual(out[0]["rule"], "unattributed_motion")


class TestPredict(unittest.TestCase):
    def test_walk_distance_predicts_the_measured_undershoot(self):
        """The robot stops ~4.1cm short every time; predicting 1.00 m is wrong."""
        p = kinematic_predict("walk_distance", {"distance_cm": 100}, {"x": 0, "y": 0, "yaw": 0}, "agibot_d1w")
        self.assertAlmostEqual(p.end_pose[0], 0.959, places=2)

    def test_walk_duration_has_a_fixed_overhead(self):
        """Measured: 100cm took 11.2s. distance/v alone predicts 7.1s."""
        p = kinematic_predict("walk_distance", {"distance_cm": 100}, {}, "agibot_d1w")
        self.assertAlmostEqual(p.duration_s, 11.2, delta=0.5)
        p150 = kinematic_predict("walk_distance", {"distance_cm": 150}, {}, "agibot_d1w")
        self.assertAlmostEqual(p150.duration_s, 14.7, delta=0.6)

    def test_backward_walk_goes_backward(self):
        p = kinematic_predict("walk_distance", {"distance_cm": 50, "direction": "backward"},
                              {"x": 0, "y": 0, "yaw": 0}, "agibot_d1w")
        self.assertAlmostEqual(p.end_pose[0], -0.459, places=2)

    def test_speed_is_a_fraction_on_the_d1w_not_metres_per_second(self):
        """speed=1.0 for 1s must predict max_vx (0.4 m), never 1.0 m."""
        p = kinematic_predict("move_robot", {"direction": "forward", "duration": 1.0, "speed": 1.0},
                              {"x": 0, "y": 0, "yaw": 0}, "agibot_d1w")
        self.assertAlmostEqual(p.end_pose[0], 0.4, places=2)

    def test_turn_changes_yaw_not_position(self):
        p = kinematic_predict("move_robot", {"direction": "turn_left", "duration": 1.0, "speed": 0.5},
                              {"x": 0, "y": 0, "yaw": 0}, "agibot_d1w")
        self.assertAlmostEqual(p.end_pose[0], 0.0, places=3)
        self.assertGreater(p.end_pose[2], 0.5)

    def test_gestures_predict_no_translation(self):
        p = kinematic_predict("do_gesture", {"gesture": "nod"}, {"x": 2, "y": 1, "yaw": 0}, "agibot_d1w")
        self.assertEqual(p.distance_m, 0.0)
        self.assertFalse(p.predicted_breach)

    def test_unknown_distance_is_unknown_not_safe(self):
        p = kinematic_predict("guide_to", {"destination": "pools"}, {"x": 0, "y": 0, "yaw": 0}, "agibot_d1w")
        self.assertEqual(p.confidence, 0.0)
        self.assertIn("not predicted safe", p.reason)

    def test_path_into_a_forbidden_zone_is_a_predicted_breach(self):
        zones = [{"zone": "pool_deck", "forbidden": True, "x0": 1.5, "x1": 4.0, "y0": -2.0, "y1": 2.0}]
        p = kinematic_predict("walk_distance", {"distance_cm": 300}, {"x": 0, "y": 0, "yaw": 0},
                              "agibot_d1w", zones=zones)
        self.assertTrue(p.predicted_breach)
        self.assertIn("pool_deck", p.zones_crossed)

    def test_path_stopping_short_of_a_zone_is_clean(self):
        zones = [{"zone": "pool_deck", "forbidden": True, "x0": 1.5, "x1": 4.0, "y0": -2.0, "y1": 2.0}]
        p = kinematic_predict("walk_distance", {"distance_cm": 100}, {"x": 0, "y": 0, "yaw": 0},
                              "agibot_d1w", zones=zones)
        self.assertFalse(p.predicted_breach)

    def test_deviation_matches_the_edge_formula_on_the_default_sigma(self):
        """0.5 m off the 0.10 m default sigma is 5 sigma - otel.py's definition.

        Uses an unmeasured body, because a measured body overrides the default
        with its own sigma, which is exactly what otel.py's baseline.json does.
        """
        pred = {"end_pose": [1.0, 0.0, 0.0], "duration_s": 5.0, "basis": "kinematic"}
        out = {"end_pose": [1.5, 0.0, 0.0], "duration_s": 5.0}
        self.assertAlmostEqual(verify(pred, out, "unitree_g1")["deviation_z"], 5.0, places=1)

    def test_measuring_the_body_tightens_its_sigma(self):
        """The D1W's measured spread is 0.7cm, so its sigma is 0.04m not 0.10m:
        the same error is a bigger signal once you know the robot."""
        pred = {"end_pose": [1.0, 0.0, 0.0], "duration_s": 11.2, "basis": "kinematic",
                "calibrated": True}
        out = {"end_pose": [1.5, 0.0, 0.0], "duration_s": 11.2}
        self.assertAlmostEqual(verify(pred, out, "agibot_d1w")["deviation_z"], 12.5, places=1)

    def test_a_turn_that_took_normal_time_but_ended_elsewhere_is_flagged(self):
        pred = {"end_pose": [1.0, 0.0, 0.0], "duration_s": 4.0, "basis": "kinematic"}
        out = {"end_pose": [1.0, 0.9, 0.0], "duration_s": 4.0}
        self.assertGreater(verify(pred, out, "agibot_d1w")["deviation_z"], 3.0)

    def test_arriving_as_predicted_scores_near_zero(self):
        pred = {"end_pose": [1.0, 0.0, 0.0], "duration_s": 5.0, "basis": "kinematic"}
        out = {"end_pose": [1.02, 0.01, 0.0], "duration_s": 5.1}
        self.assertLess(verify(pred, out, "agibot_d1w")["deviation_z"], 1.0)

    def test_uncalibrated_body_does_not_score_duration(self):
        """Our ignorance of the walk speed must not read as the robot misbehaving."""
        pred = {"end_pose": [1.0, 0.0, 0.0], "duration_s": 5.0, "basis": "kinematic",
                "calibrated": False}
        out = {"end_pose": [1.0, 0.0, 0.0], "duration_s": 23.3}   # 18s "late", 0 m off
        r = verify(pred, out, "unitree_g1")
        self.assertFalse(r["duration_scored"])
        self.assertEqual(r["deviation_z"], 0.0)
        self.assertGreater(r["z_duration"], 20)     # still recorded, just not scored

    def test_calibrated_body_does_score_duration(self):
        pred = {"end_pose": [1.0, 0.0, 0.0], "duration_s": 5.0, "basis": "kinematic",
                "calibrated": True}
        out = {"end_pose": [1.0, 0.0, 0.0], "duration_s": 23.3}
        r = verify(pred, out, "fake")
        self.assertTrue(r["duration_scored"])
        self.assertGreater(r["deviation_z"], 20)

    def test_position_deviation_fires_even_uncalibrated(self):
        """Geometry needs no baseline: ending somewhere else is always a finding."""
        pred = {"end_pose": [1.0, 0.0, 0.0], "duration_s": 5.0, "basis": "kinematic",
                "calibrated": False}
        out = {"end_pose": [1.0, 0.8, 0.0], "duration_s": 5.0}
        self.assertGreater(verify(pred, out, "agibot_d1w")["deviation_z"], 3.0)

    def test_the_d1w_is_now_calibrated_from_real_measurements(self):
        p = kinematic_predict("walk_distance", {"distance_cm": 100}, {}, "agibot_d1w")
        self.assertTrue(p.calibrated)

    def test_an_unmeasured_body_still_refuses_to_score_duration(self):
        p = kinematic_predict("walk_distance", {"distance_cm": 100}, {}, "unitree_g1")
        self.assertFalse(p.calibrated)

    def test_no_pose_feed_means_unverified_not_anomalous(self):
        pred = {"end_pose": [1.0, 0.0, 0.0], "duration_s": 5.0, "calibrated": False}
        r = verify(pred, {"end_pose": None, "duration_s": 5.1}, "agibot_d1w")
        self.assertFalse(r["position_scored"])
        self.assertIsNone(r["deviation_z"])       # None, never 0.0

    def test_unverifiable_outcome_is_surfaced_as_its_own_finding(self):
        c = Correlator(Policy())
        c.feed(ev("intent", {"tool": "walk_distance"}, ts=100, corr="a"))
        out = c.feed(ev("outcome", {"tool": "walk_distance", "deviation_z": None}, ts=110, corr="a"))
        self.assertIn("unverified_action", [b["rule"] for b in out])

    def test_unconfigured_judge_reports_unavailable_never_safe(self):
        j = CosmosJudge(url="")
        r = j.judge("walk_distance", {"distance_cm": 100}, {})
        self.assertFalse(r["available"])
        self.assertNotIn("unsafe", r)       # absence of a verdict is not a verdict

    def test_brain_judge_without_a_key_is_unavailable(self):
        self.assertFalse(BrainJudge(brain_url="http://x", key="").configured)
        r = BrainJudge(brain_url="http://x", key="").judge("walk_distance", {}, {})
        self.assertFalse(r["available"])
        self.assertNotIn("unsafe", r)

    def test_brain_judge_refuses_a_non_jpeg(self):
        r = BrainJudge(brain_url="http://x", key="k").judge("walk_distance", {}, {}, b"notjpeg")
        self.assertFalse(r["available"])
        self.assertIn("JPEG", r["reason"])

    def test_a_nested_stop_verdict_is_read_as_unsafe(self):
        """The real relay returns {"ok","judged","verdict":{...}}. Reading the
        top level found no `stop` and reported APPROVED on a real 0.98
        confidence STOP. This is that bug, pinned."""
        body = {"ok": True, "judged": True, "verdict": {
            "stop": True, "confidence": 0.98, "leaves_go_zone": False,
            "hazards": ["backpack", "blue object"],
            "reason": "backpack and blue object are in the path, so must stop"}}
        r = _fake_brain(body).judge("walk_distance", {}, {}, b"\xff\xd8x")
        self.assertTrue(r["available"])
        self.assertTrue(r["unsafe"])
        self.assertEqual(r["confidence"], 0.98)
        self.assertIn("backpack", r["hazards"])

    def test_a_nested_clear_verdict_is_read_as_safe(self):
        body = {"ok": True, "judged": True, "verdict": {
            "stop": False, "leaves_go_zone": False, "hazards": [], "confidence": 0.9,
            "reason": "path is clear"}}
        r = _fake_brain(body).judge("walk_distance", {}, {}, b"\xff\xd8x")
        self.assertTrue(r["available"])
        self.assertFalse(r["unsafe"])

    def test_leaves_go_zone_alone_is_unsafe(self):
        body = {"ok": True, "judged": True,
                "verdict": {"leaves_go_zone": True, "confidence": 0.8, "reason": "exits zone"}}
        self.assertTrue(_fake_brain(body).judge("walk_distance", {}, {}, b"\xff\xd8x")["unsafe"])

    def test_flags_clear_but_prose_unsafe_resolves_to_unsafe(self):
        """Observed live: stop=false while the reason read "forward is unsafe
        because the person is in the path". The cautious reading must win."""
        body = {"ok": True, "judged": True, "verdict": {
            "stop": False, "leaves_go_zone": False, "confidence": 0.8,
            "reason": "forward is unsafe because the person is in the path"}}
        r = _fake_brain(body).judge("walk_distance", {}, {}, b"\xff\xd8x")
        self.assertTrue(r["unsafe"])
        self.assertTrue(r["contradiction"])
        self.assertFalse(r["flags_unsafe"])

    def test_a_genuinely_clear_verdict_is_not_flagged_as_contradictory(self):
        body = {"ok": True, "judged": True, "verdict": {
            "stop": False, "leaves_go_zone": False, "confidence": 0.9,
            "reason": "the corridor ahead is clear and empty"}}
        r = _fake_brain(body).judge("walk_distance", {}, {}, b"\xff\xd8x")
        self.assertFalse(r["unsafe"])
        self.assertFalse(r["contradiction"])

    def test_an_unrecognised_verdict_shape_fails_closed(self):
        """Not understanding the answer must never read as approval."""
        for body in ({"ok": True, "judged": True, "verdict": {"something_else": 1}},
                     {"ok": True, "judged": True},
                     {"ok": True, "judged": True, "verdict": "yes"}):
            r = _fake_brain(body).judge("walk_distance", {}, {}, b"\xff\xd8x")
            self.assertFalse(r["available"], body)
            self.assertNotIn("unsafe", r)

    def test_filed_but_not_judged_is_not_approval(self):
        r = _fake_brain({"ok": True, "judged": False}).judge("walk_distance", {}, {}, b"\xff\xd8x")
        self.assertFalse(r["available"])
        self.assertNotIn("unsafe", r)

    def test_brain_judge_unreachable_is_not_approval(self):
        r = BrainJudge(brain_url="http://127.0.0.1:9", key="k", timeout_s=0.3).judge(
            "walk_distance", {}, {}, b"\xff\xd8fake")
        self.assertFalse(r["available"])

    def test_unreachable_judge_is_unavailable_not_approval(self):
        j = CosmosJudge(url="http://127.0.0.1:9", timeout_s=0.3)
        r = j.judge("walk_distance", {"distance_cm": 100}, {})
        self.assertFalse(r["available"])


class TestVenueGuardrails(unittest.TestCase):
    """The operator's document is the source of truth. These pin the three
    rules it states in prose: config can only tighten, unset is forbidden,
    unsigned means cautious."""

    @classmethod
    def setUpClass(cls):
        import venue
        cls.v = venue.load(str(Path(__file__).parent / "venue" / "leukerbad_therme.yaml"))

    def test_the_wet_and_undressed_areas_are_restricted_and_camera_off(self):
        for name in ("pool_deck", "sauna_world", "relax_level"):
            z = self.v.zones[name]
            self.assertTrue(z.restricted, name)
            self.assertTrue(z.camera_off, name)
            self.assertTrue(z.reason, f"{name} must say WHY, for the operator")

    def test_the_stairwell_is_never_enterable(self):
        self.assertTrue(self.v.zones["stairs"].restricted)

    def test_a_point_in_no_described_zone_is_restricted_not_allowed(self):
        z = self.v.zone_at(500, 500)
        self.assertEqual(z.name, "unknown")
        self.assertTrue(z.restricted)
        self.assertTrue(z.camera_off)

    def test_no_pose_is_also_treated_as_restricted(self):
        self.assertTrue(self.v.zone_at(None, None).restricted)

    def test_zones_resolve_by_polygon(self):
        self.assertEqual(self.v.zone_at(2, 2).name, "reception")
        self.assertEqual(self.v.zone_at(10, 8).name, "pool_deck")
        self.assertEqual(self.v.zone_at(16, 9).name, "sauna_world")

    def test_an_unsigned_venue_refuses_to_guide_anywhere(self):
        self.assertFalse(self.v.signed)
        ok, why = self.v.may_guide_to("reception")
        self.assertFalse(ok)
        self.assertIn("not signed", why)

    def test_a_restricted_zone_is_never_a_destination(self):
        self.assertNotIn("pool_deck", self.v.destinations)
        self.assertFalse(self.v.may_guide_to("pool_deck")[0])

    def test_config_cannot_raise_a_speed_above_the_ceiling(self):
        import venue as vm
        z = vm.Zone(name="z", speed_cap=9.0)
        v = vm.Venue(signed_off_by="someone", limits={"max_speed": 0.4},
                     zones={"z": z})
        z.polygon = [[0, 0], [1, 0], [1, 1], [0, 1]]
        z.verified = "on_site"
        self.assertLessEqual(v.speed_cap_at(0.5, 0.5), 0.4)

    def test_unverified_zones_run_slow_whatever_they_ask_for(self):
        """Every zone here is `verified: plan`, so none may run at its own cap."""
        self.assertEqual(self.v.zones["reception"].speed_cap, 0.3)
        self.assertEqual(self.v.speed_cap_at(2, 2), self.v.unverified_speed)

    def test_the_child_rules_exist_and_are_the_operators_to_set(self):
        p = self.v.person_policy()
        self.assertEqual(p["child_height_under_m"], 1.25)
        self.assertEqual(p["child_freeze_within_m"], 2.0)
        self.assertTrue(p["child_forbid_limb_gestures"])
        self.assertGreater(p["child_latch_s"], p["latch_s"])   # small people latch longer

    def test_loading_reports_what_is_wrong_rather_than_hiding_it(self):
        joined = " ".join(self.v.warnings).lower()
        self.assertIn("not signed", joined)
        self.assertIn("verified", joined)


class TestChildProximity(unittest.TestCase):
    def test_moving_near_a_child_is_critical(self):
        c = Correlator(Policy())
        c.feed(ev("intent", {"tool": "walk_distance"}, ts=100, corr="a"))
        out = c.feed(ev("state", {"moving": True, "speed_mps": 0.18,
                                  "child_near": True, "nearest_person_m": 1.4}, ts=100.5))
        b = [x for x in out if x["rule"] == "child_proximity"]
        self.assertTrue(b)
        self.assertEqual(b[0]["severity"], "critical")

    def test_a_still_robot_near_a_child_is_not_a_breach(self):
        c = Correlator(Policy())
        out = c.feed(ev("state", {"moving": False, "speed_mps": 0.0,
                                  "child_near": True, "nearest_person_m": 0.8}, ts=100))
        self.assertEqual([x for x in out if x["rule"] == "child_proximity"], [])


class TestFrameGrabber(unittest.TestCase):
    def test_camera_off_zone_refuses_to_read_at_all(self):
        """pool_deck and the changing rooms are not 'blur it later' - no frame
        may be captured there in the first place (PRIVACY.md)."""
        g = FrameGrabber(rtsp="rtsp://example/test")
        self.assertEqual(g.grab(zone="pool_deck"), b"")
        self.assertIn("camera_off", g.last_error)
        self.assertEqual(g.grab(zone="changing"), b"")

    def test_allowed_zone_is_not_refused_for_policy_reasons(self):
        g = FrameGrabber(rtsp="")                     # unconfigured, so it stops early
        g.grab(zone="reception")
        self.assertNotIn("camera_off", g.last_error)

    def test_unconfigured_grabber_fails_quietly_with_a_reason(self):
        g = FrameGrabber(rtsp="")
        self.assertEqual(g.grab(), b"")
        self.assertIn("ROBOT_RTSP", g.last_error)

    def test_a_dead_camera_never_raises(self):
        """The gate must survive a dead camera; it must not crash the tap."""
        g = FrameGrabber(rtsp="rtsp://127.0.0.1:9/nothing")
        self.assertEqual(g.grab(), b"")
        self.assertTrue(g.last_error)

    def test_warm_frame_is_refused_in_a_camera_off_zone(self):
        g = FrameGrabber(rtsp="rtsp://x/y")
        g._latest, g._latest_ts = b"\xff\xd8warm", __import__("time").time()
        self.assertEqual(g.cached(zone="pool_deck"), (b"", -1.0))
        jpeg, age = g.cached(zone="reception")
        self.assertEqual(jpeg, b"\xff\xd8warm")
        self.assertLess(age, 1)

    def test_a_stale_warm_frame_is_not_reused(self):
        g = FrameGrabber(rtsp="", max_age_s=2.0)
        g._latest, g._latest_ts = b"\xff\xd8old", __import__("time").time() - 60
        self.assertEqual(g.grab(), b"")        # falls through, does not serve a minute-old view

    def test_frame_size_cap_is_the_documented_one(self):
        import frame
        self.assertEqual((frame.MAX_W, frame.MAX_H), (640, 360))


if __name__ == "__main__":
    unittest.main(verbosity=2)
