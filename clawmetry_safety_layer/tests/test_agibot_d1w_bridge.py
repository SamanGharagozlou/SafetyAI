"""AgiBot D1 Ultra-W bridge (bridge/agibot_d1w_bridge.py): ros2 transport against a fake rclpy standing in for the
SafetyAI team's d1_cmd_vel_bridge node, the sdk transport against a fake SDK module, and the mock twin under the
skills. Caps, the SDK exact-zero rule, gait lock, stop never lies down, forbidden calls unreachable, tilt breach,
state-timeout handover, sdk keepalive, odom -> zone transform and landmark re-fix."""

from __future__ import annotations

import math
import os
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["CLAWMETRY_OTLP"] = "http://127.0.0.1:1"

import pytest
from fake_ros import FakeRos, Msg, imu, odom

from bridge import load_config
from bridge.agibot_d1w_bridge import AgibotD1WBridge, CommandRefused, SdkTransport, shape
from bridge.zones import fit_rigid_2d


def cfg(**over):
    c = load_config("config/d1w.yaml")
    c.update(over)
    return c


class TeamNode:
    """Publishes what d1_cmd_vel_bridge publishes (odom / imu / battery / ctrl_mode / connected) at 20 Hz."""

    def __init__(self, ros: FakeRos):
        self.ros = ros
        self.pose = [0.0, 0.0, 0.0]
        self.mode = 1
        self.roll = self.pitch = 0.0
        self.connected = True
        self.alive = True
        self._run = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._run:
            n = self.ros.node
            if n is not None and self.alive:
                n.feed("/connected", _val(self.connected))
                if self.connected:
                    n.feed("/odom", odom(*self.pose))
                    n.feed("/imu", imu(self.roll, self.pitch))
                    n.feed("/ctrl_mode", _val(self.mode))
                    b = Msg()
                    b.percentage = 0.83
                    n.feed("/battery", b)
            time.sleep(0.05)

    def close(self):
        self._run = False


def _val(v):
    m = Msg()
    m.data = v
    return m


@pytest.fixture
def d1w():
    ros = FakeRos()
    team = TeamNode(ros)
    b = AgibotD1WBridge(cfg(transport="ros2"), ros=ros)
    ev: list = []
    b.event_sink = lambda kind, **d: ev.append((kind, d))
    b.connect("", 0)
    time.sleep(0.2)
    yield b, ros, team, ev
    b.close()
    team.close()


def twists(ros):
    return [(m.linear.x, m.linear.y, m.angular.z) for m in ros.node.pubs["/cmd_vel"].sent]


# ------------------------------------------------------------------ shaping
def test_shape_caps_and_exact_zero():
    assert shape(1.0, 0.05, 0.4) == 0.4 and shape(-2.0, 0.05, 0.4) == -0.4
    assert shape(0.04, 0.05, 0.4) == 0.0 and shape(-0.09, 0.1, 0.15) == 0.0
    assert shape(float("nan"), 0.05, 0.4) == 0.0 and shape(float("inf"), 0.05, 0.4) == 0.0
    assert shape(0.3, 0.05, 0.4) == 0.3


def test_config_cannot_raise_caps():
    b = AgibotD1WBridge(cfg(transport="mock", d1w={"caps": {"vx": 3.7, "vy": 1.0, "wz": 3.0}}))
    assert b.cap == (0.4, 0.15, 0.6)


def test_ros2_caps_and_exact_zero_on_the_wire(d1w):
    b, ros, _, _ = d1w
    b.move_velocity(1.0, 0.05, 2.0)  # vy below the SDK minimum -> exactly 0
    time.sleep(0.15)
    vx, vy, wz = twists(ros)[-1]
    assert (vx, vy, wz) == (0.4, 0.0, 0.6)
    b.move_velocity(0.03, -0.5, -0.05)
    time.sleep(0.15)
    assert twists(ros)[-1] == (0.0, -0.15, 0.0)
    assert all(abs(x) <= 0.4 and abs(y) <= 0.15 and abs(z) <= 0.6 for x, y, z in twists(ros))
    # every published component is either exactly 0 or at/above the SDK minimum
    assert all(v == 0.0 or abs(v) >= m for t in twists(ros) for v, m in zip(t, (0.05, 0.1, 0.1), strict=True))


def test_20hz_stream_and_deadman(d1w):
    b, ros, _, _ = d1w
    pub = ros.node.pubs["/cmd_vel"]
    n0 = len(pub.sent)
    for _ in range(10):
        b.move_velocity(0.3, 0.0, 0.0)
        time.sleep(0.05)
    rate = (len(pub.sent) - n0) / 0.5
    assert 14 <= rate <= 26, rate
    time.sleep(0.45)  # no new command for > cmd_timeout_s (0.3 s) -> zeros
    assert twists(ros)[-1] == (0.0, 0.0, 0.0)


def test_gait_is_only_ever_move_and_forbidden_topics_absent(d1w):
    b, ros, _, _ = d1w
    b.move_velocity(0.3, 0, 0)
    b.stop()
    b.damp()
    gait = [m.data for m in ros.node.pubs["/gait"].sent]
    assert gait == ["move"]
    assert not any(t.rstrip("/").endswith(("robot_cmd", "attitude_cmd")) for t in ros.node.pubs)


def test_stop_zeroes_at_once_and_never_lies_down(d1w):
    b, ros, _, ev = d1w
    b.move_velocity(0.3, 0.0, 0.3)
    time.sleep(0.1)
    n0 = len(ros.node.pubs["/cmd_vel"].sent)
    b.stop()
    sent = twists(ros)[n0:]
    assert sent[:3] == [(0.0, 0.0, 0.0)] * 3  # synchronous, before the next stream tick
    b.damp()  # allow_damp false -> zero velocity only
    assert ev[-1][0] == "damp.refused"
    assert ros.node.clients == {}  # no posture service was even looked up
    with pytest.raises(CommandRefused):
        b.t._trigger("lie_down")
    with pytest.raises(CommandRefused):
        b.t._trigger("passive")
    assert not any(n.endswith(("lie_down", "passive")) for n in ros.node.clients)


def test_damp_only_with_literal_true():
    ros = FakeRos()
    team = TeamNode(ros)
    b = AgibotD1WBridge(cfg(transport="ros2", allow_damp="true"), ros=ros)  # a string is not true
    b.connect("", 0)
    time.sleep(0.15)
    b.damp()
    assert ros.node.clients == {}
    b.close()
    ros2 = FakeRos()
    team.ros = ros2
    b2 = AgibotD1WBridge(cfg(transport="ros2", allow_damp=True), ros=ros2)
    b2.connect("", 0)
    time.sleep(0.15)
    b2.damp()
    assert list(ros2.node.clients) == ["/d1_cmd_vel_bridge/damp"]
    b2.close()
    team.close()


def test_stand_without_auto_stand_only_checks(d1w):
    b, ros, team, _ = d1w
    b.stand()  # ctrl_mode 1: already standing, nothing sent
    team.mode = 51  # lying
    time.sleep(0.15)
    with pytest.raises(RuntimeError, match="not standing"):
        b.stand()
    with pytest.raises(CommandRefused, match="not standing"):
        b.move_velocity(0.3, 0, 0)
    assert ros.node.clients == {}


def test_auto_stand_calls_stand_up():
    ros = FakeRos()
    team = TeamNode(ros)
    team.mode = 51
    b = AgibotD1WBridge(cfg(transport="ros2", auto_stand=True), ros=ros)
    b.connect("", 0)
    time.sleep(0.15)
    with pytest.raises(RuntimeError):  # the fake Trigger response has success=Msg (not True)
        b.stand()
    assert list(ros.node.clients) == ["/d1_cmd_vel_bridge/stand_up"]
    b.close()
    team.close()


def test_tilt_breach_stops_and_refuses(d1w):
    b, ros, team, ev = d1w
    b.move_velocity(0.3, 0, 0)
    team.pitch = math.radians(20)
    time.sleep(0.2)
    assert twists(ros)[-1] == (0.0, 0.0, 0.0)
    assert any(k == "tilt_breach" for k, _ in ev)
    assert not b.state().balance_ok
    with pytest.raises(CommandRefused, match="tilt"):
        b.move_velocity(0.3, 0, 0)
    team.pitch = 0.0
    time.sleep(1.2)  # level again for LEVEL_AGAIN_S
    b.move_velocity(0.3, 0, 0)
    assert any(k == "tilt_cleared" for k, _ in ev)


def test_state_timeout_is_one_handover_event(d1w):
    b, ros, team, ev = d1w
    b.move_velocity(0.3, 0, 0)
    team.alive = False  # their node / the link goes quiet
    time.sleep(1.4)
    kinds = [k for k, _ in ev]
    assert kinds.count("handover") == 1
    assert twists(ros)[-1] == (0.0, 0.0, 0.0)
    s = b.state()
    assert s.mode == "no_state" and not s.balance_ok
    with pytest.raises(CommandRefused, match="no fresh"):
        b.move_velocity(0.3, 0, 0)
    team.alive = True
    time.sleep(0.3)
    assert "state_restored" in [k for k, _ in ev]


def test_disconnected_flag_hands_over(d1w):
    b, _, team, ev = d1w
    team.connected = False
    time.sleep(1.3)
    assert any(k == "handover" and d["reason"] == "robot reports disconnected" for k, d in ev)


def test_odom_to_zone_and_landmark_refix(d1w):
    b, _, team, ev = d1w
    b.set_odom_to_zone([2.0, 1.0, math.pi / 2])
    team.pose = [1.0, 0.0, 0.0]
    time.sleep(0.15)
    s = b.state()
    assert (round(s.x, 3), round(s.y, 3), round(s.yaw, 3)) == (2.0, 2.0, round(math.pi / 2, 3))
    team.pose = [3.0, 4.0, 0.2]
    time.sleep(0.15)
    r = b.refix_at_landmark("pillar")
    s = b.state()
    lx, ly = b.landmarks["pillar"]["xy"]
    assert math.hypot(s.x - lx, s.y - ly) < 1e-6 and r["drift_m"] > 0
    assert ev[-1][0] == "pose.refix"
    with pytest.raises(KeyError):
        b.refix_at_landmark("floor_tape")


def test_fit_rigid_2d_three_landmarks_with_noise():
    th, t = 0.7, (3.0, -2.0)
    c, s = math.cos(th), math.sin(th)
    src = [(0.0, 0.0), (9.2, 0.4), (13.9, 1.8)]
    dst = [
        (c * x - s * y + t[0] + e, s * x + c * y + t[1] - e) for (x, y), e in zip(src, (0.02, -0.03, 0.01), strict=True)
    ]
    tx, ty, th2, rms = fit_rigid_2d(src, dst)
    assert abs(th2 - th) < 0.01 and abs(tx - t[0]) < 0.05 and abs(ty - t[1]) < 0.05 and rms < 0.05


# ------------------------------------------------------------------ sdk transport
class FakeSdk:
    class HighLevel:
        calls: list = []

        def initRobot(self, *a):
            self.calls.append(("initRobot", a))

        def checkConnect(self):
            return True

        def move(self, vx, vy, wz):
            self.calls.append(("move", (vx, vy, wz), time.time()))
            return 0

        def standUp(self):
            self.calls.append(("standUp",))
            return 0

        def lieDown(self):
            raise AssertionError("lieDown must never be called")

        def passive(self):
            raise AssertionError("passive must never be called without allow_damp")

        def getPosition(self):
            return [0.0, 0.0, 0.0]

        def getQuaternion(self):
            return [1.0, 0.0, 0.0, 0.0]

        def getBodyVelocity(self):
            return [0.0, 0.0, 0.0]

        def getCurrentCtrlmode(self):
            return 1

        def getBatteryPower(self):
            return 76


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_sdk_keepalive_while_idle_and_caps():
    FakeSdk.HighLevel.calls = []
    port = _free_port()
    c = cfg(transport="sdk", _test_sdk=True, d1w={"sdk": {"local_ip": "127.0.0.1", "local_port": port}})
    b = AgibotD1WBridge(c, sdk=FakeSdk)
    b.connect("", 0)
    time.sleep(1.05)
    moves = [x for x in FakeSdk.HighLevel.calls if x[0] == "move"]
    assert len(moves) >= 9 and all(m[1] == (0.0, 0.0, 0.0) for m in moves)  # >= 10 Hz while idle
    gaps = [b2[2] - a[2] for a, b2 in zip(moves, moves[1:], strict=False)]
    assert max(gaps) < 0.3  # never anywhere near the 3 s lie-down rule
    b.move_velocity(5.0, 0.0, 0.0)
    time.sleep(0.12)
    assert [x for x in FakeSdk.HighLevel.calls if x[0] == "move"][-1][1] == (0.4, 0.0, 0.0)
    assert b.state().battery_pct == 76
    b.close()


def test_sdk_refused_when_someone_owns_the_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    c = cfg(transport="sdk", _test_sdk=True, d1w={"sdk": {"local_ip": "127.0.0.1", "local_port": port}})
    b = AgibotD1WBridge(c, sdk=FakeSdk)
    with pytest.raises(CommandRefused, match="ONE SDK client"):
        b.connect("", 0)
    s.close()


def test_sdk_needs_explicit_operator_choice(monkeypatch):
    monkeypatch.delenv("D1W_TRANSPORT", raising=False)
    t = SdkTransport(cfg(transport="sdk"), {"sdk": {"local_ip": "127.0.0.1", "local_port": _free_port()}}, sdk=FakeSdk)
    with pytest.raises(CommandRefused, match="D1W_TRANSPORT=sdk"):
        t.start()


# ------------------------------------------------------------------ mock twin under the skills
def test_mock_twin_navigate_and_policy(monkeypatch):
    monkeypatch.setenv("D1W_TRANSPORT", "mock")
    import skills.skills as sks

    c = cfg(mock_time_scale=6.0, time_compression=6.0)
    sk = sks.Skills(c)
    try:
        assert sk.bridge.body == "agibot_d1w_mock" and sk.bridge.state().zone == "reception"
        r = sk.navigate_to("concourse", max_s=15)
        assert r.ok, r.message
        assert r.confirmed_state["zone"] == "concourse"
        r = sk.navigate_to("pool_deck")
        assert not r.ok and "blocked_by_policy" in r.message
        sent = sk.bridge.t.sent
        assert all(abs(x) <= 0.4 and abs(y) <= 0.15 and abs(z) <= 0.6 for x, y, z in sent)
        r = sk.pick("towels")  # quadruped: the load rides on its back (say + confirm)
        assert r.ok, r.message
    finally:
        sk.bridge.close()
