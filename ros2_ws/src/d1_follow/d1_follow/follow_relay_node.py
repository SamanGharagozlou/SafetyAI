"""
ROS 2 node: relay /follow/cmd_vel to /cmd_vel, only while explicitly armed.

Off at startup. Arm/disarm with the Trigger services /d1_follow/drive_enable and
/d1_follow/drive_disable (or ``ros2 run d1_follow drive on|off``). While armed it forwards
a capped, forward-only velocity at 10 Hz when the follow state is TRACKING; see relay_core.
Stopping this process stops the robot (zero on shutdown, bridge watchdog as the backstop).
"""

import json
import sys
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import String
from std_srvs.srv import Trigger

from .relay_core import RelayGate, RelayParams
from .ros_io import OUT_QOS

PERIOD_S = 0.1


class FollowRelay(Node):
    def __init__(self) -> None:
        super().__init__('d1_follow_relay')
        d = RelayParams()
        decl = self.declare_parameter
        params = RelayParams(
            max_vx=decl('max_vx', d.max_vx).value,
            max_wz=decl('max_wz', d.max_wz).value,
            cmd_timeout_s=decl('cmd_timeout_s', d.cmd_timeout_s).value,
            state_timeout_s=decl('state_timeout_s', d.state_timeout_s).value,
            arm_timeout_s=decl('arm_timeout_s', d.arm_timeout_s).value)
        cmd_topic = decl('cmd_vel_topic', '/cmd_vel').value
        self._gate = RelayGate(params)
        if (self._gate.p.max_vx, self._gate.p.max_wz) != (abs(params.max_vx), abs(params.max_wz)):
            self.get_logger().warning(
                f'speed limits capped to vx={self._gate.p.max_vx} wz={self._gate.p.max_wz}')
        self._last_reason = None

        self._pub = self.create_publisher(Twist, cmd_topic, 10)
        self.create_subscription(Twist, '/follow/cmd_vel', self._on_cmd, OUT_QOS)
        self.create_subscription(String, '/follow/state', self._on_state, OUT_QOS)
        self.create_service(Trigger, '/d1_follow/drive_enable', self._srv_enable)
        self.create_service(Trigger, '/d1_follow/drive_disable', self._srv_disable)
        self.create_timer(PERIOD_S, self._tick)
        self.get_logger().info(
            f'ready, DISARMED -> {cmd_topic} (vx<={self._gate.p.max_vx} m/s, '
            f'|wz|<={self._gate.p.max_wz} rad/s, arm expires after {params.arm_timeout_s:g}s)')

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def _on_cmd(self, msg: Twist) -> None:
        self._gate.on_cmd(self._now(), msg.linear.x, msg.angular.z)

    def _on_state(self, msg: String) -> None:
        try:
            state = json.loads(msg.data).get('state')
        except (ValueError, AttributeError):
            return
        self._gate.on_state(self._now(), state)

    def _publish(self, vx: float, wz: float) -> None:
        cmd = Twist()
        cmd.linear.x, cmd.angular.z = float(vx), float(wz)
        self._pub.publish(cmd)

    def _tick(self) -> None:
        now = self._now()
        out = self._gate.output(now)
        if out is not None:
            self._publish(*out)
        reason = self._gate.reason(now)
        if reason != self._last_reason:
            self._last_reason = reason
            self.get_logger().info(f'relay: {reason}')

    def stop(self) -> None:
        self._gate.disarm()
        self._publish(0.0, 0.0)

    def _srv_enable(self, request, response):
        now = self._now()
        if not self._gate.state_fresh(now):
            response.success, response.message = False, (
                'no fresh /follow/state: is target_follower running?')
            return response
        self._gate.arm(now)
        response.success = True
        response.message = f'armed: {self._gate.reason(now)}'
        self.get_logger().warning('ARMED: /follow/cmd_vel now drives /cmd_vel while TRACKING')
        return response

    def _srv_disable(self, request, response):
        self._gate.disarm()
        self._publish(0.0, 0.0)
        response.success, response.message = True, 'disarmed'
        self.get_logger().warning('DISARMED')
        return response


def drive_main() -> int:
    """CLI: ``ros2 run d1_follow drive on|off``."""
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    if arg not in ('on', 'off'):
        print('usage: ros2 run d1_follow drive on|off', file=sys.stderr)
        return 2
    service = '/d1_follow/drive_enable' if arg == 'on' else '/d1_follow/drive_disable'
    rclpy.init()
    node = rclpy.create_node('d1_follow_drive_cli')
    try:
        client = node.create_client(Trigger, service)
        if not client.wait_for_service(timeout_sec=3.0):
            print(f'{service} not available (is follow_relay running?)', file=sys.stderr)
            return 1
        future = client.call_async(Trigger.Request())
        rclpy.spin_until_future_complete(node, future, timeout_sec=5.0)
        if not future.done():
            print(f'{service}: no response', file=sys.stderr)
            return 1
        print(future.result().message)
        return 0 if future.result().success else 1
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def main(args=None) -> int:
    rclpy.init(args=args)
    node = None
    try:
        node = FollowRelay()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            try:
                node.stop()
            except Exception:
                pass
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
