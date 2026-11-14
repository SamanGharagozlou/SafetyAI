"""Safety supervisor: FSM transitions and latching, L1 geofence, L2 causes, L3 verdicts, link loss (never faster),
hold at waypoint, recovery into the go zone on the mock D1W twin, approvals (ClawMetry protocol), and the ClawMetry
Stop / Reset mapping."""

from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["CLAWMETRY_OTLP"] = "http://127.0.0.1:1"

import pytest

from perception.observation import Boundary, Observation
from safety.approvals import Approvals
from safety.fsm import CAUTION, HOLD, NORMAL, RECOVER, SafetyFSM, SafetyHold
from safety.link import LinkMonitor
from safety.supervisor import SafetySupervisor


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ FSM
def test_fsm_caution_expires_back_to_normal():
    ev, clk = [], Clock()
    f = SafetyFSM(emit=lambda k, **d: ev.append((k, d)), clock=clk)
    f.raise_cause("person_near", "L2", "caution", "person at 1.5 m", ttl=2.0)
    assert f.state == CAUTION and f.speed_cap() == 0.15
    clk.t += 2.1
    assert f.state == NORMAL and f.speed_cap() is None
    kinds = [(d["from"], d["to"], d["source"]) for k, d in ev if k == "fsm.transition"]
    assert kinds == [(NORMAL, CAUTION, "L2"), (CAUTION, NORMAL, "operator")]


def test_fsm_nonpolicy_hold_expires_policy_hold_latches():
    clk = Clock()
    f = SafetyFSM(clock=clk)
    f.raise_cause("person_stop", "L2", "hold", "person at 0.7 m", ttl=1.0)
    assert f.state == HOLD and f.speed_cap() == 0.0 and not f.allows_motion()
    clk.t += 1.1
    assert f.state == NORMAL
    f.raise_cause("l3", "L3", "hold", "judge: wet floor ahead", policy=True, ttl=0.5)
    clk.t += 5
    assert f.state == HOLD  # latched despite the ttl
    assert not f.approve("x")  # no approval before recovery
    assert f.reset() is True and f.state == RECOVER
    f.recovered(True)
    assert f.awaiting_approval and f.state == RECOVER
    assert f.approve("operator") and f.state == NORMAL


def test_fsm_deny_keeps_holding_and_failed_recovery():
    f = SafetyFSM()
    f.raise_cause("l1", "L1", "hold", "predicted exit", policy=True)
    f.reset()
    f.recovered(True)
    f.deny("operator")
    assert f.state == HOLD and "denied" in f.snapshot()["causes"][0]["reason"]
    f.reset()
    f.recovered(False, "no go zone behind")
    assert f.state == HOLD


def test_fsm_rejects_bad_source():
    with pytest.raises(ValueError):
        SafetyFSM().raise_cause("x", "cloud", "hold", "?")


# ------------------------------------------------------------------ edge stack on the mock twin
@pytest.fixture
def edge(monkeypatch):
    monkeypatch.setenv("D1W_TRANSPORT", "mock")
    from bridge import load_config
    from skills.skills import Skills

    cfg = load_config("config/d1w.yaml")
    cfg["start_pose"] = [10.0, 5.0, math.pi / 2]  # bath entrance, facing the turnstiles / pool deck
    sk = Skills(cfg)
    ev: list = []

    class Sink:
        def event(self, kind, data):
            ev.append((kind, data))

        def turn_begin(self, *a):
            pass

        def turn_end(self, *a):
            pass

    sk.sinks.append(Sink())
    sup = SafetySupervisor(sk, cfg)
    time.sleep(0.15)
    yield sk, sup, ev
    sk.bridge.close()


def kinds(ev):
    return [k for k, _ in ev]


def test_l1_predicted_go_zone_exit_latches_hold(edge):
    sk, sup, ev = edge
    from skills.gate import PredictedBreach

    sk.gate.move_velocity(0.2, 0.0, 0.0)  # 2 s at 0.2 m/s from y=5.0 -> y=5.4: inside, sent
    assert sup.fsm.state == NORMAL
    sk.gate.stop()
    sk.bridge.set_odom_to_zone([10.0, 5.7, math.pi / 2])  # 0.3 m before the turnstiles
    time.sleep(0.05)
    with pytest.raises(PredictedBreach):
        sk.gate.move_velocity(0.2, 0.0, 0.0)  # the 2 s prediction crosses y=6 (pool deck): never sent
    assert sup.fsm.state == HOLD and sup.fsm.snapshot()["latched"]["source"] == "L1"
    bp = [d for k, d in ev if k == "breach_predicted"]
    assert bp and bp[-1]["source"] == "L1"
    assert {b["rule"] for b in bp[-1]["breaches"]} >= {"leaves_go_zone"}
    with pytest.raises(SafetyHold):
        sk.gate.move_velocity(0.1, 0.0, 0.0)
    r = sk.navigate_to("concourse")
    assert not r.ok and "held by safety" in r.message


def test_speed_breach_is_a_refusal_not_a_hold(edge):
    sk, sup, _ = edge
    from skills.gate import PredictedBreach

    with pytest.raises(PredictedBreach):
        sk.gate.move_velocity(0.3, 0.0, 0.0)  # over the 0.2 m/s bath_entrance cap
    assert sup.fsm.state == NORMAL


def test_refused_plan_is_not_a_hold(edge):
    sk, sup, _ = edge
    r = sk.navigate_to("pool_deck")
    assert not r.ok and "blocked_by_policy" in r.message
    assert sup.fsm.state == NORMAL


def test_caution_caps_speed_and_link_loss_never_faster(edge):
    sk, sup, ev = edge
    sup.on_link("brain", False, {"brain_error": "timeout"})
    assert sup.fsm.state == CAUTION and "link_lost" in kinds(ev)
    vx, vy, w = sup.filter_velocity(0.4, 0.0, 0.3)
    assert math.isclose(vx, 0.15) and w == 0.3
    sup.on_link("control_plane", False, {})
    assert sup.filter_velocity(0.4, 0.0, 0.0)[0] <= 0.15
    sup.on_link("brain", True, {})
    assert "link_restored" in kinds(ev)
    assert sup.fsm.state == CAUTION  # control plane still down
    with pytest.raises(SafetyHold, match="waypoint"):
        sup.at_waypoint("concourse")  # hold_at_waypoint (default policy)
    assert sup.fsm.state == HOLD
    sup.on_link("control_plane", True, {})
    assert sup.fsm.state == NORMAL


def test_continue_caution_policy(edge):
    sk, sup, _ = edge
    sup.on_cp_loss = "continue_caution"
    sup.on_link("control_plane", False, {})
    sup.at_waypoint("concourse")  # no hold
    assert sup.fsm.state == CAUTION


def test_l2_person_and_boundary(edge):
    sk, sup, ev = edge
    sup.on_observation(Observation(nearest_person_m=1.6, people_within_2m=1))
    assert sup.fsm.state == CAUTION and ev[-1][0] in ("person_near", "fsm.transition")
    sup.on_observation(Observation(nearest_person_m=0.7, people_within_2m=1))
    assert sup.fsm.state == HOLD and not sup.fsm.snapshot()["latched"]  # not policy-related
    assert any(k == "person_near" and d["level"] == "stop" for k, d in ev)
    sup.on_observation(Observation(degraded=["detector_stale", "l2_people_disabled"]))
    assert any(c["key"] == "l2_degraded" for c in sup.fsm.causes())
    # a no-go floor ahead while standing: hazard event + judge request, no hold
    b = Boundary("floor_transition", ahead_m=1.0, floor_here="concourse_tile", floor_ahead="wet_deck", no_go_ahead=True)
    sup.on_observation(Observation(boundary=b))
    assert "hazard" in kinds(ev) and sup.take_judge_flag() == "hazard"


def test_l3_verdicts(edge):
    sk, sup, ev = edge
    sup.on_verdict(
        {
            "leaves_go_zone": False,
            "hazards": ["wet floor"],
            "people_within_2m": 0,
            "stop": False,
            "confidence": 0.8,
            "reason": "puddle",
        }
    )
    assert sup.fsm.state == CAUTION and "judge_verdict" in kinds(ev)
    sup.on_verdict({"judge_unavailable": True, "error": "timeout"})
    assert sup.judge_unavailable()
    sup.on_verdict(
        {
            "leaves_go_zone": True,
            "hazards": [],
            "people_within_2m": 0,
            "stop": True,
            "confidence": 0.3,
            "reason": "unsure",
        }
    )
    assert sup.fsm.state != HOLD  # low confidence: no hold
    sup.on_verdict(
        {
            "leaves_go_zone": False,
            "hazards": [],
            "people_within_2m": 0,
            "stop": True,
            "confidence": 0.9,
            "reason": "path is clear",
        }
    )
    assert sup.fsm.state == CAUTION  # inconsistent bare stop: caution, not a latched hold
    sup.on_verdict({"stop": True, "hazards": ["black chair"], "confidence": 0.9, "reason": "objects do not obstruct"})
    assert sup.fsm.state == CAUTION  # a stop with objects is still caution: L1 / L2 own the holds
    sup.on_verdict(
        {
            "leaves_go_zone": True,
            "hazards": [],
            "people_within_2m": 0,
            "stop": True,
            "confidence": 0.9,
            "reason": "pool edge",
        }
    )
    assert sup.fsm.state == HOLD and sup.fsm.snapshot()["latched"]["source"] == "L3"
    assert len(sup.verdicts) == 6


def test_judge_required_blocks_guide_when_unavailable(edge):
    sk, sup, _ = edge
    sup.judge_required = True
    sup.on_link("brain", False, {})
    assert "judge" in (sup.check_skill("guide_to") or "")
    assert sup.check_skill("say") is None


def test_breach_recover_approve_cycle(edge):
    sk, sup, ev = edge
    t = sk.bridge.t
    # the robot ended up just past the turnstiles (y = 6.2 > 6.0), facing in: staged breach
    sk.bridge.set_odom_to_zone([10.0, 6.2, math.pi / 2])
    t.set_pose(0.0, 0.0, 0.0)
    time.sleep(0.1)
    assert sk.bridge.state().zone == "pool_deck"
    sup.fsm.raise_cause("l3", "L3", "hold", "judge: past the turnstiles", policy=True)
    out = sup.reset("operator", wait=True)
    assert out["latched"]
    s = sk.bridge.state()
    assert sup.go.inside_with_margin(s.x, s.y, 0.3), (s.x, s.y)
    assert s.y < 6.0 - 0.29 and math.hypot(s.x - 10.0, s.y - 6.2) <= 1.5 + 0.05
    assert all(x <= 0 and y == 0 and z == 0 for x, y, z in sk.bridge.t.sent if x or y or z)  # only backwards
    assert sup.fsm.state == RECOVER and sup.fsm.awaiting_approval
    rec = [d for k, d in ev if k == "recover"]
    assert [d["phase"] for d in rec] == ["plan", "done"] and rec[-1]["ok"]
    with pytest.raises(SafetyHold):
        sk.gate.move_velocity(0.2, 0, 0)  # not before approval
    assert sup.approve("console:eaby")
    assert sup.fsm.state == NORMAL


def test_recover_refuses_when_go_zone_not_behind(edge):
    sk, sup, ev = edge
    sk.bridge.set_odom_to_zone([11.0, 10.0, math.pi / 2])  # deep in the pool deck: 4 m from the go zone
    time.sleep(0.05)
    sup.fsm.raise_cause("l1", "L1", "hold", "x", policy=True)
    sup.reset(wait=True)
    assert sup.fsm.state == HOLD
    assert not any(any(v) for v in sk.bridge.t.sent[-5:])


# ------------------------------------------------------------------ ClawMetry Stop / Reset
def test_clawmetry_stop_holds_reset_rearms(edge, tmp_path):
    from skills.clawmetry_robotics import SkillsController

    sk, sup, ev = edge
    reg, jd = tmp_path / "c", tmp_path / "j"
    reg.mkdir(mode=0o700)
    jd.mkdir(mode=0o700)
    c = SkillsController(sk, directory=reg, journal_dir=jd, observe_hz=0)
    try:
        st = c._public_status()
        assert st["capabilities"]["physical_stop"] is True and "zero cmd_vel" in st["adapter"]
        c._do_stop(1, "clawmetry_stop")
        assert sup.fsm.state == HOLD and c._status["physical_stop_confirmed"]
        assert sk.navigate_to("concourse").ok is False
        c.generation = 1
        c.reset(1)
        time.sleep(0.2)
        assert sup.fsm.state == NORMAL  # operator stop inside the go zone: Reset re-arms directly
        assert sk.bridge.state().mode != "damped"  # never damped / lay down
    finally:
        c.close()


# ------------------------------------------------------------------ approvals (ClawMetry pre-tool receiver protocol)
def test_approvals_pending_then_decision():
    calls = []
    answers = [
        {"status": "pending", "approval_id": "ap1", "retry_after_ms": 200},
        {"status": "pending", "approval_id": "ap1", "retry_after_ms": 200},
        {
            "status": "decided",
            "hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "Denied by eaby"},
        },
    ]

    def post(p):
        calls.append(dict(p))
        return answers.pop(0)

    r = Approvals("http://127.0.0.1:8900", post=post).request("mcp__g1-skills__guide_to", {"poi": "pool_deck"})
    assert r["decision"] == "deny" and r["approval_id"] == "ap1"
    assert "approval_id" not in calls[0] and calls[1]["approval_id"] == "ap1"  # no duplicate rows
    assert calls[0]["tool_name"] == "mcp__g1-skills__guide_to" and calls[0]["hook_event_name"] == "PreToolUse"


def test_approvals_no_rule_and_down_are_not_approvals():
    no_rule = lambda p: {
        "status": "decided",
        "hookSpecificOutput": {"permissionDecision": "allow", "permissionDecisionReason": "no matching policy"},
    }  # noqa: E731
    assert Approvals("http://x", post=no_rule).request("t", {})["decision"] == "no_rule"

    def down(p):
        raise ConnectionError("refused")

    assert Approvals("http://x", post=down).request("t", {}, timeout_s=10)["decision"] == "unavailable"


# ------------------------------------------------------------------ link monitor
def test_link_monitor_flags(tmp_path):
    hb = tmp_path / "sync.heartbeat"
    hb.write_text("x")
    seen = []
    state = {"up": True}

    def probe():
        if not state["up"]:
            raise TimeoutError("brain")
        return {}

    m = LinkMonitor(
        "http://spark:8780",
        "k",
        hb,
        on_change=lambda n, ok, d: seen.append((n, ok)),
        probe=probe,
        control_plane_max_age_s=5,
    )
    m.tick()
    assert m.brain_ok and m.control_plane_ok and seen == [("brain", True), ("control_plane", True)]
    state["up"] = False
    m.tick()  # one missed beat is tolerated
    assert m.brain_ok
    m.tick()
    assert m.brain_ok is False and ("brain", False) in seen
    os.utime(hb, (time.time() - 60, time.time() - 60))
    m.tick()
    assert m.control_plane_ok is False
