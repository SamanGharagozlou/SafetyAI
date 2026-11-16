"""In-process ROS test: the relay is silent until armed and only drives while TRACKING."""

import json
import time

import pytest

rclpy = pytest.importorskip('rclpy')

from geometry_msgs.msg import Twist                                          # noqa: E402
from rclpy.executors import SingleThreadedExecutor                           # noqa: E402
from rclpy.node import Node                                                  # noqa: E402
from std_msgs.msg import String                                              # noqa: E402
from std_srvs.srv import Trigger                                             # noqa: E402

from d1_follow.follow_relay_node import FollowRelay                          # noqa: E402
from d1_follow.ros_io import OUT_QOS                                         # noqa: E402


class Harness(Node):
    def __init__(self):
        super().__init__('relay_test_harness')
        self.pub_cmd = self.create_publisher(Twist, '/follow/cmd_vel', OUT_QOS)
        self.pub_state = self.create_publisher(String, '/follow/state', OUT_QOS)
        self.out = []
        self.create_subscription(Twist, '/cmd_vel', self.out.append, 10)

    def send(self, state, vx=0.2, wz=0.1):
        self.pub_state.publish(String(data=json.dumps({'state': state})))
        cmd = Twist()
        cmd.linear.x, cmd.angular.z = vx, wz
        self.pub_cmd.publish(cmd)


@pytest.fixture
def rig():
    rclpy.init()
    relay, harness = FollowRelay(), Harness()
    ex = SingleThreadedExecutor()
    ex.add_node(relay)
    ex.add_node(harness)

    def spin(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ex.spin_once(timeout_sec=0.02)

    yield relay, harness, spin
    relay.destroy_node()
    harness.destroy_node()
    rclpy.shutdown()


def test_silent_until_armed_then_drives_while_tracking_then_stops(rig):
    relay, harness, spin = rig
    harness.send('TRACKING')
    spin(0.5)
    assert harness.out == []  # disarmed: nothing on /cmd_vel

    res = relay._srv_enable(None, Trigger.Response())
    assert res.success
    for _ in range(5):
        harness.send('TRACKING')
        spin(0.1)
    moving = [m for m in harness.out if m.linear.x > 0]
    assert moving and all(m.linear.x <= 0.3 and abs(m.angular.z) <= 0.5 for m in moving)

    harness.out.clear()
    harness.send('LOST')
    spin(0.5)
    assert harness.out and harness.out[0].linear.x == 0.0
    assert len(harness.out) == 1  # one stop, then silent


def test_arm_refused_without_follow_state(rig):
    relay, _, spin = rig
    spin(0.2)
    res = relay._srv_enable(None, Trigger.Response())
    assert not res.success
    assert not relay._gate.armed


def test_disarm_publishes_zero(rig):
    relay, harness, spin = rig
    harness.send('TRACKING')
    spin(0.2)
    relay._srv_enable(None, Trigger.Response())
    harness.out.clear()
    relay._srv_disable(None, Trigger.Response())
    spin(0.2)
    assert harness.out and harness.out[-1].linear.x == 0.0
    assert not relay._gate.armed
