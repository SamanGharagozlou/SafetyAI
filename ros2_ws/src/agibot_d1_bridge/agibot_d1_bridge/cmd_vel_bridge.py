"""ROS 2 cmd_vel -> AgiBot D1 Edu Ultra bridge (zsl-1 point-foot / zsl-1w wheel-foot).

Subscribes to geometry_msgs/Twist on `cmd_vel` and forwards it to the AgiBot
SDK's HighLevel.move(vx, vy, yaw_rate). Includes a command watchdog and
Trigger services for stand_up / lie_down / passive (damping, emergency).
"""
import importlib
import os
import platform
import sys

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from std_srvs.srv import Trigger

SDK_SUBMODULE = os.path.join('third_party', 'agibot_D1_Edu-Ultra')


def _find_sdk_lib(override: str, robot_type: str) -> str:
    arch = platform.machine().replace('amd64', 'x86_64').replace('arm64', 'aarch64')
    candidates = []
    if override:
        candidates.append(override)
    if os.environ.get('AGIBOT_SDK_DIR'):
        candidates.append(os.path.join(os.environ['AGIBOT_SDK_DIR'], 'lib', robot_type, arch))
    here = os.path.dirname(os.path.realpath(__file__))
    while here != os.path.dirname(here):
        candidates.append(os.path.join(here, SDK_SUBMODULE, 'lib', robot_type, arch))
        here = os.path.dirname(here)
    for c in candidates:
        if os.path.isdir(c):
            return c
    raise FileNotFoundError(
        'AgiBot SDK lib not found. Run `git submodule update --init`, or set '
        'AGIBOT_SDK_DIR / the sdk_lib_dir parameter. Tried: ' + ', '.join(candidates))


def _shape(v: float, vmin: float, vmax: float) -> float:
    """Clamp to +-vmax; values below vmin must be sent as exactly 0 (SDK rule)."""
    if abs(v) < vmin:
        return 0.0
    return max(-vmax, min(vmax, v))


class CmdVelBridge(Node):
    def __init__(self):
        super().__init__('d1_cmd_vel_bridge')
        p = self.declare_parameter
        p('robot_type', 'zsl-1w')   # 'zsl-1' (point-foot) or 'zsl-1w' (wheel-foot)
        p('local_ip', '192.168.234.100')
        p('local_port', 43988)
        p('robot_ip', '192.168.234.1')
        p('sdk_lib_dir', '')
        p('cmd_vel_topic', 'cmd_vel')
        p('cmd_timeout', 0.5)       # s without cmd_vel -> stop
        p('rate', 20.0)             # Hz send rate
        p('auto_stand', False)      # stand up on startup
        # Conservative defaults, well inside the SDK limits (vx 3.0-3.7 / vy 1.0 / wz 3.0).
        p('max_vx', 1.0)
        p('max_vy', 0.5)
        p('max_wz', 1.5)
        # SDK minimums: below these the command must be 0.
        p('min_vx', 0.05)
        p('min_vy', 0.1)
        p('min_wz', 0.1)            # 0.02 on zsl-1, 0.1 on zsl-1w
        g = lambda n: self.get_parameter(n).value  # noqa: E731

        rtype = g('robot_type')
        if rtype not in ('zsl-1', 'zsl-1w'):
            raise ValueError(f"robot_type must be 'zsl-1' or 'zsl-1w', got {rtype!r}")
        lib = _find_sdk_lib(g('sdk_lib_dir'), rtype)
        sys.path.insert(0, lib)
        sdk = importlib.import_module('mc_sdk_' + rtype.replace('-', '_') + '_py')
        self.robot = sdk.HighLevel()
        self.robot.initRobot(g('local_ip'), int(g('local_port')), g('robot_ip'))
        self.get_logger().info(
            f'SDK from {lib}; robot {g("robot_ip")} (local {g("local_ip")}:{g("local_port")})')

        self.lim = {k: g(k) for k in
                    ('max_vx', 'max_vy', 'max_wz', 'min_vx', 'min_vy', 'min_wz')}
        self.timeout = float(g('cmd_timeout'))
        self.cmd = (0.0, 0.0, 0.0)
        self.last_cmd_time = None
        self.was_moving = False
        self.stale_warned = False

        self.create_subscription(Twist, g('cmd_vel_topic'), self._on_twist, 10)
        self.create_service(Trigger, '~/stand_up', lambda q, r: self._srv(r, 'standUp'))
        self.create_service(Trigger, '~/lie_down', lambda q, r: self._srv(r, 'lieDown'))
        self.create_service(Trigger, '~/passive', lambda q, r: self._srv(r, 'passive'))
        self.create_timer(1.0 / float(g('rate')), self._tick)

        if g('auto_stand'):
            self._call('standUp')

    def _call(self, name, *args):
        try:
            ret = getattr(self.robot, name)(*args)
        except Exception as e:  # SDK/network errors must not kill the node
            self.get_logger().error(f'{name} raised: {e}')
            return None
        if ret not in (0, None):
            self.get_logger().warn(f'{name} returned 0x{int(ret):X}', throttle_duration_sec=2.0)
        return ret

    def _srv(self, resp, name):
        ret = self._call(name)
        resp.success = ret == 0
        resp.message = f'{name} -> {ret}'
        return resp

    def _on_twist(self, msg: Twist):
        L = self.lim
        self.cmd = (_shape(msg.linear.x, L['min_vx'], L['max_vx']),
                    _shape(msg.linear.y, L['min_vy'], L['max_vy']),
                    _shape(msg.angular.z, L['min_wz'], L['max_wz']))
        self.last_cmd_time = self.get_clock().now()
        self.stale_warned = False

    def _tick(self):
        stale = (self.last_cmd_time is None or
                 (self.get_clock().now() - self.last_cmd_time).nanoseconds * 1e-9 > self.timeout)
        if stale and self.was_moving and not self.stale_warned:
            self.get_logger().warn('cmd_vel timed out, stopping')
            self.stale_warned = True
        cmd = (0.0, 0.0, 0.0) if stale else self.cmd
        moving = any(cmd)
        if moving:
            self._call('move', *cmd)
        elif self.was_moving:
            self._call('move', 0.0, 0.0, 0.0)  # single stop on transition
        self.was_moving = moving

    def stop(self):
        self._call('move', 0.0, 0.0, 0.0)


def main():
    rclpy.init()
    node = CmdVelBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
