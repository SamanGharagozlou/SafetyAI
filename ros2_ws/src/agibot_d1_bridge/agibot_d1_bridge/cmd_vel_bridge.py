"""ROS 2 cmd_vel -> AgiBot D1 Edu Ultra bridge (zsl-1 point-foot / zsl-1w wheel-foot).

Topics in : cmd_vel (Twist)        -> move/crawl/climb(vx, vy, wz) for the active gait
            attitude_cmd (Twist)   -> attitudeControl(roll_vel=ang.x, pitch_vel=ang.y,
                                      yaw_vel=ang.z, height_vel=lin.z)
            gait (String)          -> move | crawl | climb
            robot_cmd (String)     -> stand_up | lie_down | damp | shake_hand
                                      | cancel_crawl | cancel_climb
Services  : ~/stand_up ~/lie_down ~/damp ~/passive ~/shake_hand (std_srvs/Trigger)
Topics out: imu, odom, battery, joint_states, ctrl_mode, connected
All velocity-style commands have a watchdog: silence -> stop.
"""
import importlib
import os
import platform
import sys

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import BatteryState, Imu, JointState
from std_msgs.msg import Bool, Int32, String
from std_srvs.srv import Trigger

SDK_SUBMODULE = os.path.join('third_party', 'agibot_D1_Edu-Ultra')

# SDK limits per robot type and gait: ((min, max) for vx, vy, wz).
# Below `min` the command must be exactly 0 (SDK rule).
LIMITS = {
    'zsl-1': {'move': ((0.05, 3.0), (0.1, 1.0), (0.02, 3.0))},
    'zsl-1w': {
        'move': ((0.05, 3.7), (0.1, 1.0), (0.1, 3.0)),
        'crawl': ((0.1, 3.0), (0.1, 0.5), (0.1, 1.0)),
        'climb': ((0.1, 1.0), (0.1, 1.0), (0.1, 3.0)),
    },
}
ATT_MAX = 0.5   # attitudeControl: rad/s for roll/pitch/yaw, m/s for height
LEGS = ('FR', 'FL', 'RR', 'RL')
JOINTS = ('Abad', 'Hip', 'Knee', 'Foot')


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
    vmax = max(vmax, vmin)  # a cap below the SDK minimum would be invalid
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
        p('cmd_timeout', 0.5)       # s without cmd_vel/attitude_cmd -> stop
        p('rate', 20.0)             # Hz command send rate
        p('state_rate', 10.0)       # Hz sensor publish rate
        p('auto_stand', False)      # stand up on startup
        p('gait', 'move')           # initial gait: move | crawl | climb
        # Your own (conservative) caps; the SDK per-gait limits always apply on top.
        p('max_vx', 1.0)
        p('max_vy', 0.5)
        p('max_wz', 1.5)
        g = lambda n: self.get_parameter(n).value  # noqa: E731

        rtype = g('robot_type')
        if rtype not in LIMITS:
            raise ValueError(f"robot_type must be one of {list(LIMITS)}, got {rtype!r}")
        self.gaits = LIMITS[rtype]
        self.gait = g('gait')
        if self.gait not in self.gaits:
            raise ValueError(f"gait must be one of {list(self.gaits)} for {rtype}")
        self.user_max = (g('max_vx'), g('max_vy'), g('max_wz'))

        lib = _find_sdk_lib(g('sdk_lib_dir'), rtype)
        sys.path.insert(0, lib)
        sdk = importlib.import_module('mc_sdk_' + rtype.replace('-', '_') + '_py')
        self.robot = sdk.HighLevel()
        self.robot.initRobot(g('local_ip'), int(g('local_port')), g('robot_ip'))
        self.get_logger().info(
            f'SDK from {lib}; robot {g("robot_ip")} (local {g("local_ip")}:{g("local_port")}); '
            f'gait={self.gait}')

        self.timeout = float(g('cmd_timeout'))
        self.cmd = (0.0, 0.0, 0.0)
        self.last_cmd_time = None
        self.was_moving = False
        self.att = (0.0, 0.0, 0.0, 0.0)
        self.last_att_time = None
        self.was_att = False
        self.stale_warned = False

        # --- inputs
        self.create_subscription(Twist, g('cmd_vel_topic'), self._on_twist, 10)
        self.create_subscription(Twist, 'attitude_cmd', self._on_attitude, 10)
        self.create_subscription(String, 'gait', lambda m: self.set_gait(m.data.strip().lower()), 10)
        self.create_subscription(String, 'robot_cmd', self._on_robot_cmd, 10)
        for srv, cmd in (('stand_up', 'stand_up'), ('lie_down', 'lie_down'),
                         ('damp', 'damp'), ('passive', 'damp'), ('shake_hand', 'shake_hand')):
            self.create_service(Trigger, '~/' + srv,
                                lambda q, r, c=cmd: self._srv(r, c))

        # --- outputs
        self.pub_imu = self.create_publisher(Imu, 'imu', 10)
        self.pub_odom = self.create_publisher(Odometry, 'odom', 10)
        self.pub_batt = self.create_publisher(BatteryState, 'battery', 10)
        self.pub_joint = self.create_publisher(JointState, 'joint_states', 10)
        self.pub_mode = self.create_publisher(Int32, 'ctrl_mode', 10)
        self.pub_conn = self.create_publisher(Bool, 'connected', 10)

        self.create_timer(1.0 / float(g('rate')), self._tick)
        self.create_timer(1.0 / float(g('state_rate')), self._publish_state)

        if g('auto_stand'):
            self._call('standUp')

    # ------------------------------------------------------------ SDK helpers
    def _call(self, name, *args):
        fn = getattr(self.robot, name, None)
        if fn is None:
            self.get_logger().error(f'SDK has no {name}() for this robot type')
            return None
        try:
            ret = fn(*args)
        except Exception as e:  # SDK/network errors must not kill the node
            self.get_logger().error(f'{name} raised: {e}')
            return None
        if ret not in (0, None):
            self.get_logger().warn(f'{name} returned 0x{int(ret):X}', throttle_duration_sec=2.0)
        return ret

    def _get(self, name):
        try:
            return list(getattr(self.robot, name)())
        except Exception:
            return None

    # --------------------------------------------------------------- commands
    def _clear_motion(self):
        self.cmd = (0.0, 0.0, 0.0)
        self.last_cmd_time = None
        self.att = (0.0, 0.0, 0.0, 0.0)
        self.last_att_time = None
        self.was_moving = False
        self.was_att = False

    def _stop(self):
        self._call(self.gait, 0.0, 0.0, 0.0)
        if self.was_att:
            self._call('attitudeControl', 0.0, 0.0, 0.0, 0.0)

    def _leave_gait(self):
        if self.gait == 'crawl':
            self._call('cancelCrawl')
        elif self.gait == 'climb':
            self._call('cancelClimb')
        self.gait = 'move'

    def set_gait(self, new):
        if new not in self.gaits:
            self.get_logger().warn(f'gait {new!r} not available; use {list(self.gaits)}')
            return False
        if new == self.gait:
            return True
        self._stop()
        self._clear_motion()
        self._leave_gait()
        self.gait = new
        self.get_logger().info(f'gait -> {new}')
        return True

    def robot_cmd(self, cmd):
        """Run a named command. Returns SDK return code, or None if unknown/failed."""
        cmd = cmd.strip().lower()
        if cmd in ('damp', 'passive'):
            # Emergency: damp first, no extra calls. Robot drops.
            self._clear_motion()
            ret = self._call('passive')
            self.gait = 'move'
            return ret
        if cmd in ('stand_up', 'stand'):
            return self._call('standUp')
        if cmd in ('lie_down', 'lie'):
            self._stop()
            self._clear_motion()
            self._leave_gait()
            return self._call('lieDown')
        if cmd in ('shake_hand', 'shakehand'):
            self._stop()
            self._clear_motion()
            return self._call('shakeHand')
        if cmd == 'cancel_crawl':
            self._clear_motion()
            self.gait = 'crawl'
            self._leave_gait()
            return 0
        if cmd == 'cancel_climb':
            self._clear_motion()
            self.gait = 'climb'
            self._leave_gait()
            return 0
        self.get_logger().warn(
            f'unknown robot_cmd {cmd!r}; use stand_up | lie_down | damp | shake_hand '
            f'| cancel_crawl | cancel_climb')
        return None

    def _on_robot_cmd(self, msg: String):
        self.robot_cmd(msg.data)

    def _srv(self, resp, cmd):
        ret = self.robot_cmd(cmd)
        resp.success = ret == 0
        resp.message = f'{cmd} -> {ret}'
        return resp

    # ------------------------------------------------------- velocity inputs
    def _on_twist(self, msg: Twist):
        lim = self.gaits[self.gait]
        vals = (msg.linear.x, msg.linear.y, msg.angular.z)
        self.cmd = tuple(
            _shape(v, lim[i][0], min(lim[i][1], self.user_max[i]))
            for i, v in enumerate(vals))
        self.last_cmd_time = self.get_clock().now()
        self.stale_warned = False

    def _on_attitude(self, msg: Twist):
        vals = (msg.angular.x, msg.angular.y, msg.angular.z, msg.linear.z)
        self.att = tuple(max(-ATT_MAX, min(ATT_MAX, v)) for v in vals)
        self.last_att_time = self.get_clock().now()

    def _age(self, t):
        return float('inf') if t is None else (self.get_clock().now() - t).nanoseconds * 1e-9

    def _tick(self):
        stale = self._age(self.last_cmd_time) > self.timeout
        if stale and self.was_moving and not self.stale_warned:
            self.get_logger().warn('cmd_vel timed out, stopping')
            self.stale_warned = True
        cmd = (0.0, 0.0, 0.0) if stale else self.cmd
        moving = any(cmd)
        if moving:
            self._call(self.gait, *cmd)
        elif self.was_moving:
            self._call(self.gait, 0.0, 0.0, 0.0)  # single stop on transition
        self.was_moving = moving

        att = (0.0,) * 4 if self._age(self.last_att_time) > self.timeout else self.att
        att_on = any(att)
        if att_on:
            self._call('attitudeControl', *att)
        elif self.was_att:
            self._call('attitudeControl', 0.0, 0.0, 0.0, 0.0)
        self.was_att = att_on

    # ------------------------------------------------------------ telemetry
    def _publish_state(self):
        connected = False
        try:
            connected = bool(self.robot.checkConnect())
        except Exception:
            pass
        self.pub_conn.publish(Bool(data=connected))
        if not connected:
            return
        now = self.get_clock().now().to_msg()

        mode = self._call_quiet('getCurrentCtrlmode')
        if mode is not None:
            self.pub_mode.publish(Int32(data=int(mode)))

        quat = self._get('getQuaternion')      # [w, x, y, z]
        gyro = self._get('getBodyGyro')
        acc = self._get('getBodyAcc')
        if quat and gyro and acc:
            m = Imu()
            m.header.stamp, m.header.frame_id = now, 'imu_link'
            m.orientation.w, m.orientation.x, m.orientation.y, m.orientation.z = quat
            m.angular_velocity.x, m.angular_velocity.y, m.angular_velocity.z = gyro
            m.linear_acceleration.x, m.linear_acceleration.y, m.linear_acceleration.z = acc
            self.pub_imu.publish(m)

        pos = self._get('getPosition')
        vel = self._get('getBodyVelocity')
        if quat and pos and vel:
            o = Odometry()
            o.header.stamp, o.header.frame_id, o.child_frame_id = now, 'odom', 'base_link'
            o.pose.pose.position.x, o.pose.pose.position.y, o.pose.pose.position.z = pos
            (o.pose.pose.orientation.w, o.pose.pose.orientation.x,
             o.pose.pose.orientation.y, o.pose.pose.orientation.z) = quat
            o.twist.twist.linear.x, o.twist.twist.linear.y, o.twist.twist.linear.z = vel
            if gyro:
                o.twist.twist.angular.x, o.twist.twist.angular.y, o.twist.twist.angular.z = gyro
            self.pub_odom.publish(o)

        batt = self._call_quiet('getBatteryPower')
        if batt is not None:
            b = BatteryState()
            b.header.stamp = now
            b.percentage = float(batt) / 100.0
            b.voltage = b.current = b.charge = b.capacity = b.design_capacity = float('nan')
            b.power_supply_status = BatteryState.POWER_SUPPLY_STATUS_UNKNOWN
            b.present = True
            self.pub_batt.publish(b)

        js = JointState()
        js.header.stamp = now
        for j in JOINTS:
            q = self._get(f'getLeg{j}Joint')
            if not q:
                continue          # e.g. zsl-1 has no foot joint
            dq = self._get(f'getLeg{j}JointVel') or [0.0] * len(q)
            tau = self._get(f'getLeg{j}JointTorque') or [0.0] * len(q)
            for i, leg in enumerate(LEGS[:len(q)]):
                js.name.append(f'{leg}_{j.lower()}')
                js.position.append(float(q[i]))
                js.velocity.append(float(dq[i]))
                js.effort.append(float(tau[i]))
        if js.name:
            self.pub_joint.publish(js)

    def _call_quiet(self, name):
        try:
            return getattr(self.robot, name)()
        except Exception:
            return None

    def stop(self):
        self._stop()


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
