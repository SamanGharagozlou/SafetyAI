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


class TestCompliance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import compliance
        cls.c = compliance.load(str(Path(__file__).parent / "compliance.yaml"))

    def test_a_child_near_miss_is_reportable_and_starts_a_clock(self):
        l = self.c.label("child_proximity", now=1000.0)
        self.assertTrue(l["reportable"])
        clocks = [o for o in l["obligations"] if o["report_by"]]
        self.assertTrue(clocks)
        self.assertLessEqual(min(o["report_by"] for o in clocks), 1000.0 + 3600)

    def test_a_telemetry_gap_is_not_a_data_breach(self):
        """Over-reporting destroys the signal. `silence` must never notify a DPA."""
        l = self.c.label("silence")
        self.assertFalse(l["reportable"])
        cites = " ".join(o["citation"] for o in l["obligations"]).lower()
        self.assertNotIn("art. 33", cites)
        self.assertNotIn("fadp", cites)

    def test_a_prevented_breach_is_evidence_not_an_incident(self):
        l = self.c.label("predicted_breach")
        self.assertFalse(l["reportable"])

    def test_tampering_with_the_record_pulls_the_data_protection_clocks(self):
        l = self.c.label("chain_broken")
        self.assertTrue(l["reportable"])
        cites = " ".join(o["citation"] for o in l["obligations"]).lower()
        self.assertIn("33", cites)      # GDPR notification
        self.assertIn("fadp", cites)    # and the Swiss one, which is primary here

    def test_an_unmapped_rule_is_not_assumed_harmless(self):
        l = self.c.label("brand_new_detector")
        self.assertIsNone(l["reportable"])
        self.assertIn("unmapped", l["labels"])

    def test_non_numeric_deadlines_never_become_zero(self):
        """'as soon as possible' must not silently turn into 'now' or 'never'."""
        import compliance
        self.assertIsNone(compliance.parse_within("as_soon_as_possible"))
        self.assertIsNone(compliance.parse_within("without_undue_delay"))
        self.assertEqual(compliance.parse_within("72h"), 72 * 3600)

    def test_readiness_reports_gaps_not_a_score(self):
        r = self.c.readiness({"ledger_chained": True})
        self.assertNotIn("score", r)
        self.assertNotIn("percent", json.dumps(r).lower())
        self.assertTrue(r["gaps"])
        self.assertIn("not a certificate", r["statement"])

    def test_the_unproven_child_detector_is_named_as_a_blocking_gap(self):
        r = self.c.readiness({"ledger_chained": True})
        blocking = [g["control"] for g in r["blocking_certification"]]
        self.assertIn("child_safety_verified", blocking)
        self.assertIn("safety_function_rated", [g["control"] for g in r["gaps"]])

    def test_every_framework_is_flagged_unverified_by_counsel(self):
        self.assertEqual(len(self.c.readiness({})["frameworks_unverified_by_counsel"]),
                         len(self.c.frameworks))

    def test_industrial_robot_standards_carry_a_scope_warning(self):
        for f in ("iso10218", "iso15066"):
            self.assertIn("scope_warning", self.c.frameworks[f], f)


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


# ----------------------------------------------------------------------
# The voice layer. Everything Vapi tells us about a conversation we cannot
# otherwise see - and the rules about what we refuse to keep.
# ----------------------------------------------------------------------

def _hub(**over):
    """A Hub with a temp ledger and nothing external configured."""
    import argparse, server
    d = tempfile.TemporaryDirectory()
    a = argparse.Namespace(
        ledger=str(Path(d.name) / "l.jsonl"), robot_id="t", speed_cap=0.4,
        brain_url="", brain_key="", judge=False, rtsp="", venue="", compliance="",
        zones="", body="agibot_d1w", guard="", upstream="http://127.0.0.1:1",
        state_url="http://127.0.0.1:1", state_token="", token="", timeout=1.0,
        state_hz=2.0, inbound_token="", redact_transcripts=False,
        host="127.0.0.1", port=0)
    for k, v in over.items():
        setattr(a, k, v)
    h = server.Hub(a)
    h._tmp = d                      # keep the directory alive with the hub
    return h


def _vmsg(**kw):
    return {"message": kw}


class TestVoiceSink(unittest.TestCase):
    def test_final_transcript_is_stored_and_becomes_who_asked(self):
        h = _hub()
        r = h.handle_vapi_event(_vmsg(type="transcript", role="user",
                                      transcriptType="final",
                                      transcript="take me to the sauna",
                                      call=  {"id": "call_1"}))
        self.assertTrue(r["stored"])
        self.assertEqual(h.last_said["text"], "take me to the sauna")
        self.assertEqual(h.last_said["call_id"], "call_1")

    def test_partial_transcript_is_counted_not_stored(self):
        h = _hub()
        r = h.handle_vapi_event(_vmsg(type="transcript", role="user",
                                      transcriptType="partial", transcript="take me"))
        self.assertFalse(r["stored"])
        self.assertIsNone(h.last_said)
        self.assertEqual(h._voice_dropped, 1)

    def test_final_only_subscription_shape_is_recognised(self):
        """Subscribing to finals gives type transcript[transcriptType="final"]
        with the field itself absent. Reading only the field drops the lot."""
        h = _hub()
        r = h.handle_vapi_event(_vmsg(type='transcript[transcriptType="final"]',
                                      role="user", transcript="where is the grotto"))
        self.assertTrue(r["stored"])
        self.assertEqual(h.last_said["text"], "where is the grotto")

    def test_the_assistant_speaking_is_not_a_guest_request(self):
        h = _hub()
        h.handle_vapi_event(_vmsg(type="transcript", role="assistant",
                                  transcriptType="final", transcript="this way please"))
        self.assertIsNone(h.last_said)

    def test_end_of_call_report_keeps_no_audio(self):
        h = _hub()
        h.handle_vapi_event(_vmsg(
            type="end-of-call-report", endedReason="hangup", durationSeconds=42,
            recordingUrl="https://example.invalid/a.wav",
            artifact={"transcript": "User: hello", "recordingUrl": "https://x/b.wav",
                      "stereoRecordingUrl": "https://x/c.wav"}))
        raw = Path(h.args.ledger).read_text()
        self.assertNotIn("a.wav", raw)
        self.assertNotIn("b.wav", raw)
        self.assertNotIn("c.wav", raw)
        d = [e for e in h.led.read() if e["kind"] == "utterance"][-1]["data"]
        self.assertTrue(d["audio_dropped"])
        self.assertEqual(d["duration_s"], 42)

    def test_end_of_call_report_keeps_the_stage_latencies(self):
        h = _hub()
        h.handle_vapi_event(_vmsg(type="end-of-call-report", endedReason="hangup",
            artifact={"performanceMetrics": {
                "transcriberLatencyAverage": 310, "endpointingLatencyAverage": 120,
                "modelLatencyAverage": 640, "voiceLatencyAverage": 250,
                "turnLatencyAverage": 1320, "numUserInterrupted": 2,
                "turnLatencies": [{}, {}, {}]}}))
        d = [e for e in h.led.read() if e["kind"] == "utterance"][-1]["data"]
        self.assertEqual(d["stage_latency_ms"]["stt_ms"], 310)
        self.assertEqual(d["stage_latency_ms"]["llm_ms"], 640)
        self.assertEqual(d["stage_latency_ms"]["turns_measured"], 3)

    def test_an_unrecognised_voice_event_is_reported_not_swallowed(self):
        h = _hub()
        r = h.handle_vapi_event(_vmsg(type="something-vapi-added-last-week", x=1))
        self.assertFalse(r["recognised"])
        d = h.led.read()[-1]
        self.assertEqual(d["kind"], "integrity")
        self.assertEqual(d["data"]["event"], "voice_event_unrecognised")

    def test_chatter_is_dropped_without_an_integrity_event(self):
        h = _hub()
        before = len(h.led.read())
        h.handle_vapi_event(_vmsg(type="speech-update", status="started"))
        self.assertEqual(len(h.led.read()), before)

    def test_redaction_keeps_the_shape_not_the_words(self):
        h = _hub(redact_transcripts=True)
        h.handle_vapi_event(_vmsg(type="transcript", role="user", transcriptType="final",
                                  transcript="my name is Anna and I am in room 204"))
        # Check the FIELDS, not the raw file. The raw text is mostly sha256
        # hashes, and asserting that a short string like "204" is absent from
        # 64 hex characters per line passes by luck: hash 391f752045fe... holds
        # "204" and failed this the day an unrelated test shifted the clock.
        said = [e for e in h.led.read() if e["kind"] == "utterance"]
        self.assertEqual(len(said), 1)
        text = said[0]["data"]["text"]
        for word in ("Anna", "204", "room"):
            self.assertNotIn(word, text)
        self.assertIn("redacted", h.last_said["text"])

    def test_vapis_clock_is_recorded_as_theirs_not_merged_into_ours(self):
        h = _hub()
        h.handle_vapi_event(_vmsg(type="transcript", role="user", transcriptType="final",
                                  transcript="hello", timestamp=1700000000000))
        e = [x for x in h.led.read() if x["kind"] == "utterance"][-1]
        self.assertAlmostEqual(e["src_ts"], 1700000000.0, places=0)
        self.assertNotEqual(round(e["skew_s"]), 0)

    def test_the_sentence_that_caused_an_action_is_bound_to_it(self):
        h = _hub()
        h._forward = lambda raw: ({"results": []}, None)
        h._poll_bridge_once = lambda: {}
        h.handle_vapi_event(_vmsg(type="transcript", role="user", transcriptType="final",
                                  transcript="walk two metres"))
        h.handle_tool("walk_distance", {"distance_m": 2}, {}, "tc1")
        evs = h.led.read()
        intent = [e for e in evs if e["kind"] == "intent"][-1]
        self.assertEqual(intent["data"]["asked"], "walk two metres")
        self.assertTrue(intent["data"]["speech_seen"])
        linked = [e for e in evs if e["kind"] == "utterance"
                  and e["data"].get("replayed")][-1]
        self.assertEqual(linked["corr"], intent["corr"])

    def test_an_old_sentence_is_not_credited_with_a_later_action(self):
        import server
        h = _hub()
        h._forward = lambda raw: ({"results": []}, None)
        h._poll_bridge_once = lambda: {}
        h.handle_vapi_event(_vmsg(type="transcript", role="user", transcriptType="final",
                                  transcript="hello there"))
        h.last_said["ts"] -= server.SPEECH_WINDOW_S + 5
        h.handle_tool("walk_distance", {"distance_m": 1}, {}, "tc2")
        intent = [e for e in h.led.read() if e["kind"] == "intent"][-1]
        self.assertNotIn("asked", intent["data"])
        self.assertTrue(intent["data"]["speech_seen"])   # the sink IS live

    def test_judge_mode_starts_without_touching_frames_too_early(self):
        """--judge once called frames.start() before self.frames existed, so the
        judged collector died at boot on an AttributeError."""
        h = _hub(judge=True)
        self.assertIsNotNone(h.frames)


class TestGovernedRestSurface(unittest.TestCase):
    """The robot's own REST routes are what the operator console drives. They
    go through the same gate as a voice tool call, or the ledger is blind to
    half the commands the robot receives."""

    def _hub_with_zone(self):
        import server
        h = _hub()
        zp = Path(h._tmp.name) / "zones.json"
        zp.write_text(json.dumps([{"zone": "pool_deck", "forbidden": True,
                                   "x0": 1.5, "x1": 4.0, "y0": -2.0, "y1": 2.0}]))
        h.zones = server.Hub._load_zones(str(zp))
        h.last_state = {"x": 0.0, "y": 0.0, "yaw": 0.0, "has_pose": True}
        h._poll_bridge_once = lambda: h.last_state
        return h

    def test_a_direct_move_is_recorded_as_rest_not_as_voice(self):
        h = self._hub_with_zone()
        h._forward_path = lambda path, body: ({"ok": True}, None, 200)
        code, out = h.handle_rest("/move", {"direction": "forward", "speed": 0.2,
                                            "duration": 1})
        self.assertEqual(code, 200)
        intent = [e for e in h.led.read() if e["kind"] == "intent"][-1]
        self.assertEqual(intent["data"]["source"], "rest_direct")
        self.assertEqual(intent["data"]["tool"], "move_robot")

    def test_a_direct_move_into_a_forbidden_zone_is_refused_with_403(self):
        h = self._hub_with_zone()
        reached = []
        h._forward_path = lambda path, body: (reached.append(path), ({}, None, 200))[1]
        code, out = h.handle_rest("/move", {"direction": "forward", "speed": 1.0,
                                            "duration": 10})
        self.assertEqual(code, 403)
        self.assertEqual(out["refused_by"], "clawmetry")
        self.assertEqual(reached, [])          # the robot never heard it
        d = [e for e in h.led.read() if e["kind"] == "decision"][-1]["data"]
        self.assertEqual(d["decision"], "deny")

    def test_stop_is_never_held_up_by_the_gate(self):
        h = self._hub_with_zone()
        sent = []
        h._forward_path = lambda path, body: (sent.append(path), ({"ok": True}, None, 200))[1]
        code, _ = h.handle_rest("/stop", {})
        self.assertEqual(code, 200)
        self.assertEqual(sent, ["/stop"])
        self.assertEqual([e for e in h.led.read() if e["kind"] == "prediction"], [])

    def test_a_robot_refusal_is_reported_as_a_refusal_not_as_unreachable(self):
        """The live robot answers 400 to a gesture while it is not standing.
        Reporting that as "unreachable" sends the guest the wrong reason and
        hides a working robot behind a network-looking error."""
        import io, urllib.error
        from unittest import mock
        h = _hub()
        err = urllib.error.HTTPError(
            "http://up/vapi", 400, "Bad Request", {},
            io.BytesIO(b'{"ok":false,"error":"cannot nod while not standing"}'))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            reply, e = h._forward({"message": {}})
        self.assertIn("cannot nod while not standing", reply["results"][0]["result"])
        self.assertNotIn("unreachable", reply["results"][0]["result"])
        self.assertIn("400", e)

    def test_the_robots_own_status_code_reaches_the_caller(self):
        import io, urllib.error
        from unittest import mock
        h = self._hub_with_zone()
        err = urllib.error.HTTPError("http://up/gesture", 400, "Bad Request", {},
                                     io.BytesIO(b'{"ok":false,"error":"not standing"}'))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            code, out = h.handle_rest("/gesture", {"gesture": "nod"})
        self.assertEqual(code, 400)
        self.assertEqual(out["error"], "not standing")

    def test_the_mapping_covers_exactly_the_robots_direct_routes(self):
        """d1-vapi-server's own `direct` map, as of the live server."""
        import server
        self.assertEqual(set(server.REST_AS_TOOL),
                         {"/move", "/command", "/gait", "/gesture", "/stop"})
        self.assertNotIn("/walk", server.REST_AS_TOOL)   # not a direct route there

    def test_read_only_paths_are_passed_through_not_gated(self):
        import server
        self.assertEqual(server.PASSTHROUGH_GET, {"/health", "/status", "/call"})


class TestInboundAuth(unittest.TestCase):
    def _h(self, hub, path="/", **headers):
        import server
        o = object.__new__(server.Handler)
        o.hub = hub
        o.path = path
        o.headers = {k.replace("_", "-"): v for k, v in headers.items()}
        return o

    def test_no_token_configured_lets_everything_through(self):
        hub = _hub()
        self.assertTrue(self._h(hub)._authed())

    def test_bearer_header_is_accepted(self):
        hub = _hub(inbound_token="s3cret")
        self.assertTrue(self._h(hub, authorization="Bearer s3cret")._authed())

    def test_bare_header_value_is_accepted(self):
        hub = _hub(inbound_token="s3cret")
        self.assertTrue(self._h(hub, authorization="s3cret")._authed())

    def test_vapi_secret_header_is_accepted(self):
        hub = _hub(inbound_token="s3cret")
        self.assertTrue(self._h(hub, x_vapi_secret="s3cret")._authed())

    def test_query_key_is_accepted_for_a_browser(self):
        hub = _hub(inbound_token="s3cret")
        self.assertTrue(self._h(hub, path="/?k=s3cret")._authed())

    def test_cookie_is_accepted_so_the_key_leaves_the_url(self):
        hub = _hub(inbound_token="s3cret")
        self.assertTrue(self._h(hub, cookie="a=b; cm_k=s3cret")._authed())

    def test_a_wrong_token_is_refused(self):
        hub = _hub(inbound_token="s3cret")
        self.assertFalse(self._h(hub, authorization="Bearer nope")._authed())
        self.assertFalse(self._h(hub)._authed())


class TestAttribution(unittest.TestCase):
    """The attribution detector stays silent until there is speech to attribute
    to - otherwise a deployment without the voice sink flags every command."""

    def test_silent_before_any_transcript_has_arrived(self):
        c = Correlator()
        out = c.feed(ev("intent", {"tool": "walk_distance"}, corr="a"))
        self.assertEqual(out, [])

    def test_fires_once_speech_is_live_and_a_command_has_none(self):
        c = Correlator()
        c.feed(ev("utterance", {"text": "hello", "role": "user"}, actor="guest"))
        out = c.feed(ev("intent", {"tool": "walk_distance"}, corr="a"))
        self.assertEqual([b["rule"] for b in out], ["command_unattributed_to_speech"])
        self.assertEqual(out[0]["severity"], "low")

    def test_quiet_when_the_command_carries_the_sentence(self):
        c = Correlator()
        c.feed(ev("utterance", {"text": "hello", "role": "user"}, actor="guest"))
        out = c.feed(ev("intent", {"tool": "walk_distance", "asked": "walk"}, corr="a"))
        self.assertEqual(out, [])

    def test_a_status_query_is_not_an_unrequested_action(self):
        c = Correlator()
        c.feed(ev("utterance", {"text": "hello", "role": "user"}, actor="guest"))
        self.assertEqual(c.feed(ev("intent", {"tool": "robot_status"}, corr="a")), [])


# ---------------------------------------------------------------------------
# THE OPERATOR'S GUARDRAILS, EDITED FROM THE DASHBOARD
# ---------------------------------------------------------------------------

VENUE_SRC = Path(__file__).parent / "venue" / "leukerbad_therme.yaml"


def _venue_hub():
    """A hub pointed at a THROWAWAY COPY of the real venue file.

    A copy, not the repo's file: these tests write to it, and a test that edits
    the document the venue signs is a test that can change what the robot does.
    """
    import shutil
    h = _hub()
    dst = Path(h._tmp.name) / "venue.yaml"
    shutil.copy(VENUE_SRC, dst)
    import venue as venue_mod
    h.venue = venue_mod.load(str(dst))
    h._apply_venue()
    return h


class TestPolicyClassification(unittest.TestCase):
    """Which way does an edit move the robot's freedom."""

    def setUp(self):
        import yaml
        self.raw = yaml.safe_load(VENUE_SRC.read_text())

    def _dir(self, path, value):
        import policy
        p = policy.build_plan(self.raw, [{"path": path, "value": value}])
        self.assertEqual(p.errors, [])
        self.assertEqual(len(p.changes), 1, f"{path} produced {p.changes}")
        return p.changes[0].direction

    def test_a_higher_speed_cap_loosens(self):
        self.assertEqual(self._dir(["zones", "concourse", "speed_cap"], 0.4), "loosen")

    def test_a_lower_speed_cap_tightens(self):
        self.assertEqual(self._dir(["zones", "concourse", "speed_cap"], 0.1), "tighten")

    def test_marking_a_zone_measured_is_a_loosening(self):
        # The one that does not look like a safety change: ticking a box
        # labelled "measured" lifts the unverified clamp and can triple a speed.
        self.assertEqual(self._dir(["zones", "concourse", "verified"], "on_site"), "loosen")

    def test_signing_the_file_is_a_loosening(self):
        self.assertEqual(self._dir(["venue", "signed_off_by"], "A. Operator"), "loosen")

    def test_clearing_the_signature_tightens(self):
        self.raw["venue"]["signed_off_by"] = "A. Operator"
        self.assertEqual(self._dir(["venue", "signed_off_by"], ""), "tighten")

    def test_opening_a_restricted_zone_loosens(self):
        self.assertEqual(self._dir(["zones", "pool_deck", "restricted"], False), "loosen")

    def test_turning_the_camera_back_on_loosens(self):
        self.assertEqual(self._dir(["zones", "pool_deck", "camera_off"], False), "loosen")

    def test_requiring_approval_tightens(self):
        self.assertEqual(self._dir(["zones", "concourse", "requires_approval"], True),
                         "tighten")

    def test_stopping_further_from_a_child_tightens(self):
        self.assertEqual(self._dir(["people", "child", "freeze_within_m"], 3.0), "tighten")

    def test_letting_the_robot_closer_to_a_child_loosens(self):
        self.assertEqual(self._dir(["people", "child", "freeze_within_m"], 1.0), "loosen")

    def test_a_faster_speed_near_people_loosens(self):
        self.assertEqual(self._dir(["people", "near_person_speed"], 0.3), "loosen")

    def test_noticing_people_later_loosens(self):
        self.assertEqual(self._dir(["people", "slow_within_m"], 1.0), "loosen")

    def test_an_unknown_field_is_refused_not_ignored(self):
        import policy
        p = policy.build_plan(self.raw, [{"path": ["zones", "concourse", "colour"],
                                          "value": "blue"}])
        self.assertTrue(p.errors)
        self.assertEqual(p.changes, [])

    def test_an_unknown_zone_is_refused(self):
        import policy
        p = policy.build_plan(self.raw, [{"path": ["zones", "nowhere", "speed_cap"],
                                          "value": 0.2}])
        self.assertTrue(p.errors)

    def test_polygons_are_not_editable(self):
        import policy
        p = policy.build_plan(self.raw, [{"path": ["zones", "concourse", "polygon"],
                                          "value": [[0, 0], [1, 1]]}])
        self.assertTrue(p.errors)

    def test_setting_a_value_to_what_it_already_is_is_not_a_change(self):
        import policy
        cap = self.raw["zones"]["concourse"]["speed_cap"]
        p = policy.build_plan(self.raw, [{"path": ["zones", "concourse", "speed_cap"],
                                          "value": cap}])
        self.assertEqual(p.changes, [])
        self.assertEqual(p.errors, [])

    def test_a_cap_above_the_ceiling_is_flagged_as_clamped(self):
        import policy
        p = policy.build_plan(self.raw, [{"path": ["zones", "concourse", "speed_cap"],
                                          "value": 1.5}])
        self.assertTrue(p.changes[0].note, "a cap above the ceiling must say so")

    def test_a_zero_speed_is_refused(self):
        import policy
        p = policy.build_plan(self.raw, [{"path": ["zones", "concourse", "speed_cap"],
                                          "value": 0}])
        self.assertTrue(p.errors)


class TestSurgicalYamlWrite(unittest.TestCase):
    """The document has to survive the edit."""

    def setUp(self):
        self.raw = VENUE_SRC.read_text()

    def test_only_the_edited_line_changes(self):
        import policy
        out = policy.set_scalar(self.raw, ["zones", "concourse", "speed_cap"], 0.2)
        a, b = self.raw.split("\n"), out.split("\n")
        self.assertEqual(len(a), len(b))
        self.assertEqual(sum(1 for x, y in zip(a, b) if x != y), 1)

    def test_a_trailing_comment_survives(self):
        import policy
        out = policy.set_scalar(self.raw, ["venue", "signed_off_by"], "A. Operator")
        line = [l for l in out.split("\n") if "signed_off_by" in l][0]
        self.assertIn("#", line, "the comment explaining the field was deleted")
        self.assertIn("A. Operator", line)

    def test_a_date_is_quoted_so_it_stays_a_string(self):
        # Unquoted, YAML loads 2026-11-16 as a datetime.date: not a string, not
        # JSON serializable, and it took the whole API response down once.
        import policy, yaml, json
        out = policy.set_scalar(self.raw, ["venue", "signed_off_date"], "2026-11-16")
        val = yaml.safe_load(out)["venue"]["signed_off_date"]
        self.assertIsInstance(val, str)
        json.dumps(val)

    def test_there_is_always_a_space_before_a_comment(self):
        # Without it YAML reads the comment INTO the value, silently.
        import policy, yaml
        out = policy.set_scalar(self.raw, ["venue", "signed_off_by"], "A. Operator")
        self.assertEqual(yaml.safe_load(out)["venue"]["signed_off_by"], "A. Operator")

    def test_an_absent_key_is_inserted_into_the_right_zone(self):
        import policy, yaml
        out = policy.set_scalar(self.raw, ["zones", "concourse", "requires_approval"], True)
        z = yaml.safe_load(out)["zones"]
        self.assertIs(z["concourse"]["requires_approval"], True)
        self.assertIsNone(z["reception"].get("requires_approval"),
                          "the inserted key leaked into a neighbouring zone")

    def test_a_missing_parent_is_an_error_not_a_new_section(self):
        import policy
        with self.assertRaises(KeyError):
            policy.set_scalar(self.raw, ["zoness", "concourse", "speed_cap"], 0.2)

    def test_the_file_still_parses_after_several_edits(self):
        import policy, yaml
        out = self.raw
        for path, val in ((["zones", "concourse", "speed_cap"], 0.2),
                          (["zones", "pool_deck", "restricted"], False),
                          (["venue", "signed_off_by"], "A. Operator: Duty #1"),
                          (["people", "child", "freeze_within_m"], 2.5)):
            out = policy.set_scalar(out, path, val)
        d = yaml.safe_load(out)
        self.assertEqual(d["zones"]["concourse"]["speed_cap"], 0.2)
        self.assertIs(d["zones"]["pool_deck"]["restricted"], False)
        self.assertEqual(d["venue"]["signed_off_by"], "A. Operator: Duty #1")
        self.assertEqual(d["people"]["child"]["freeze_within_m"], 2.5)


class TestGuardrailEditing(unittest.TestCase):
    """Applying a change through the hub: who, what, and is it on the record."""

    def test_a_change_needs_a_name(self):
        h = _venue_hub()
        code, out = h.venue_apply([{"path": ["zones", "concourse", "speed_cap"],
                                    "value": 0.2}], operator="")
        self.assertEqual(code, 400)
        self.assertFalse(out["ok"])

    def test_a_loosening_needs_explicit_confirmation(self):
        h = _venue_hub()
        code, out = h.venue_apply([{"path": ["zones", "concourse", "speed_cap"],
                                    "value": 0.4}], operator="A. Operator")
        self.assertEqual(code, 409)
        self.assertTrue(out["needs_confirmation"])

    def test_a_tightening_does_not(self):
        h = _venue_hub()
        code, out = h.venue_apply([{"path": ["zones", "concourse", "speed_cap"],
                                    "value": 0.1}], operator="A. Operator")
        self.assertEqual(code, 200)
        self.assertTrue(out["ok"])

    def test_the_change_lands_on_the_ledger_with_the_name_and_the_diff(self):
        h = _venue_hub()
        h.venue_apply([{"path": ["zones", "concourse", "speed_cap"], "value": 0.1}],
                      operator="A. Operator", reason="wet floor")
        pol = [e for e in h.led.read() if e["kind"] == "policy"]
        self.assertEqual(len(pol), 1)
        d = pol[0]["data"]
        self.assertEqual(pol[0]["actor"], "operator")
        self.assertEqual(d["by"], "A. Operator")
        self.assertEqual(d["reason"], "wet floor")
        self.assertEqual(d["changes"][0]["direction"], "tighten")
        self.assertFalse(d["loosened"])

    def test_a_loosening_is_flagged_on_the_record(self):
        h = _venue_hub()
        h.venue_apply([{"path": ["zones", "concourse", "speed_cap"], "value": 0.4}],
                      operator="A. Operator", confirm_loosen=True)
        d = [e for e in h.led.read() if e["kind"] == "policy"][0]["data"]
        self.assertTrue(d["loosened"], "an audit must be able to find these without a diff")

    def test_the_previous_file_is_kept(self):
        h = _venue_hub()
        _, out = h.venue_apply([{"path": ["zones", "concourse", "speed_cap"], "value": 0.1}],
                               operator="A. Operator")
        self.assertTrue(Path(out["backup"]).exists())

    def test_the_chain_survives_a_policy_event(self):
        h = _venue_hub()
        h.venue_apply([{"path": ["zones", "concourse", "speed_cap"], "value": 0.1}],
                      operator="A. Operator")
        self.assertTrue(h.led.verify()["ok"])

    def test_the_change_takes_effect_without_a_restart(self):
        h = _venue_hub()
        h.venue_apply([{"path": ["limits", "max_speed"], "value": 0.2}],
                      operator="A. Operator")
        self.assertAlmostEqual(h.venue.max_speed, 0.2)
        self.assertAlmostEqual(h.cor.pol.speed_cap_mps, 0.2,
                               msg="the correlator is still enforcing the old ceiling")

    def test_signing_off_lifts_the_unsigned_clamp(self):
        h = _venue_hub()
        self.assertFalse(h.venue.signed)
        h.venue_apply([{"path": ["venue", "signed_off_by"], "value": "A. Operator"}],
                      operator="A. Operator", confirm_loosen=True)
        self.assertTrue(h.venue.signed)

    def test_measured_alone_does_not_raise_the_speed_while_unsigned(self):
        # Both clamps have to come off. This is the rule an operator is most
        # likely to expect to work on its own.
        h = _venue_hub()
        cfg = h.venue_config()
        before = [z for z in cfg["zones"] if z["name"] == "reception"][0]["effective_speed"]
        h.venue_apply([{"path": ["zones", "reception", "verified"], "value": "on_site"}],
                      operator="A. Operator", confirm_loosen=True)
        after = [z for z in h.venue_config()["zones"]
                 if z["name"] == "reception"][0]["effective_speed"]
        self.assertEqual(before, after)

    def test_the_config_is_json_serializable(self):
        # A datetime.date from an unquoted YAML date broke exactly this.
        h = _venue_hub()
        h.venue_apply([{"path": ["venue", "signed_off_date"], "value": "2026-11-16"}],
                      operator="A. Operator")
        json.dumps(h.venue_config())

    def test_a_bad_edit_writes_nothing(self):
        h = _venue_hub()
        before = Path(h.venue.path).read_text()
        code, _ = h.venue_apply([{"path": ["zones", "nowhere", "speed_cap"], "value": 0.2}],
                                operator="A. Operator")
        self.assertEqual(code, 400)
        self.assertEqual(Path(h.venue.path).read_text(), before)

    def test_the_effective_speed_shows_the_clamp(self):
        h = _venue_hub()
        z = [z for z in h.venue_config()["zones"] if z["name"] == "concourse"][0]
        self.assertGreater(z["speed_cap"], z["effective_speed"],
                           "the view must show that the asked-for cap is not what runs")

    def test_no_venue_means_no_editing(self):
        h = _hub()
        self.assertFalse(h.venue_config()["configured"])
        code, _ = h.venue_apply([], operator="A. Operator")
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
