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
import math
import os
import platform
import sys
import time

import rclpy
from geometry_msgs.msg import TransformStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import BatteryState, Imu, JointState
from std_msgs.msg import Bool, Int32, String
from tf2_ros import TransformBroadcaster
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


def _quat_to_rpy(w, x, y, z):
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return roll, pitch, yaw


def _rpy_to_quat(roll, pitch, yaw):
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return (cr * cp * cy + sr * sp * sy, sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy, cr * cp * sy - sr * sp * cy)   # w, x, y, z


def _wrap(a):
    return math.atan2(math.sin(a), math.cos(a))


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
        p('link_timeout', 15.0)     # exit if the SDK link stays down this long (launch respawns us); 0 = never
        p('odom_source', 'integrated')  # 'integrated' (body velocity x IMU yaw) | 'sdk' (SDK getPosition)
        p('odom_rate', 50.0)        # Hz integration rate
        p('vel_deadband', 0.02)     # m/s: body-velocity noise below this is treated as 0
        p('publish_tf', True)       # broadcast odom -> base_link TF from the SDK odometry
        p('odom_frame', 'odom')
        p('base_frame', 'base_link')
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
        self.pub_odom_sdk = self.create_publisher(Odometry, 'odom_sdk', 10)   # raw SDK position, for comparison
        self.odom_source = str(g('odom_source'))
        self.vel_deadband = float(g('vel_deadband'))
        self._connected = False
        self._connected_since = 0.0
        self.int_x = self.int_y = 0.0
        self.int_yaw0 = None
        self.int_last_t = None
        self.int_quat = None            # (w, x, y, z) with yaw relative to the start
        self.link_timeout = float(g('link_timeout'))
        self.last_link_ok = time.monotonic()
        self.publish_tf = bool(g('publish_tf'))
        self.odom_frame, self.base_frame = g('odom_frame'), g('base_frame')
        self.tf_broadcaster = TransformBroadcaster(self) if self.publish_tf else None
        self.pub_batt = self.create_publisher(BatteryState, 'battery', 10)
        self.pub_joint = self.create_publisher(JointState, 'joint_states', 10)
        self.pub_mode = self.create_publisher(Int32, 'ctrl_mode', 10)
        self.pub_conn = self.create_publisher(Bool, 'connected', 10)

        self.create_timer(1.0 / float(g('rate')), self._tick)
        self.create_timer(1.0 / float(g('state_rate')), self._publish_state)
        self.create_timer(1.0 / float(g('odom_rate')), self._integrate)

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
        if connected and not self._connected:
            self._connected_since = time.monotonic()    # integrator warms up after (re)connect
        self._connected = connected
        self.pub_conn.publish(Bool(data=connected))
        if connected:
            self.last_link_ok = time.monotonic()
        else:
            down = time.monotonic() - self.last_link_ok
            # The SDK client cannot recover from a dead UDP socket ("async_receive_from" error),
            # so exit and let the launch file respawn a fresh process.
            if self.link_timeout > 0 and down > self.link_timeout:
                self.get_logger().error(f'SDK link down for {down:.0f}s, exiting for respawn')
                # rclpy.shutdown() is not enough: the SDK's native threads keep the process alive,
                # so the launch file would never see an exit. Leave immediately (link is dead anyway).
                os._exit(1)
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
            use_int = self.odom_source == 'integrated' and self.int_quat is not None
            px, py = (self.int_x, self.int_y) if use_int else (pos[0], pos[1])
            ori = self.int_quat if use_int else tuple(quat)
            self.pub_odom.publish(self._make_odom(now, px, py, pos[2], ori, vel, gyro))
            self.pub_odom_sdk.publish(self._make_odom(now, pos[0], pos[1], pos[2], tuple(quat), vel, gyro))
            if self.tf_broadcaster is not None:
                t = TransformStamped()
                t.header.stamp, t.header.frame_id = now, self.odom_frame
                t.child_frame_id = self.base_frame
                t.transform.translation.x, t.transform.translation.y, t.transform.translation.z = px, py, pos[2]
                (t.transform.rotation.w, t.transform.rotation.x,
                 t.transform.rotation.y, t.transform.rotation.z) = ori
                self.tf_broadcaster.sendTransform(t)

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

    def _make_odom(self, stamp, x, y, z, ori, vel, gyro):
        o = Odometry()
        o.header.stamp, o.header.frame_id, o.child_frame_id = stamp, self.odom_frame, self.base_frame
        o.pose.pose.position.x, o.pose.pose.position.y, o.pose.pose.position.z = x, y, z
        (o.pose.pose.orientation.w, o.pose.pose.orientation.x,
         o.pose.pose.orientation.y, o.pose.pose.orientation.z) = ori
        o.twist.twist.linear.x, o.twist.twist.linear.y, o.twist.twist.linear.z = vel
        if gyro:
            o.twist.twist.angular.x, o.twist.twist.angular.y, o.twist.twist.angular.z = gyro
        return o

    def _integrate(self):
        """Dead-reckon x/y from the SDK body velocity rotated by the IMU yaw.

        The SDK's own getPosition jumps and stalls (see docs/mono_slam.md), so we integrate the
        body-frame velocity ourselves. Yaw is taken relative to the first reading so odom starts at 0.
        """
        if not self._connected or time.monotonic() - self._connected_since < 1.0:
            # not connected, or the first samples after (re)connect (garbage): restart from the origin
            self.int_x = self.int_y = 0.0
            self.int_yaw0 = None
            self.int_last_t = None
            return
        vel = self._get('getBodyVelocity')
        quat = self._get('getQuaternion')      # [w, x, y, z]
        if not vel or not quat:
            self.int_last_t = None
            return
        roll, pitch, yaw = _quat_to_rpy(*quat)
        if self.int_yaw0 is None:
            self.int_yaw0 = yaw
        yaw_rel = _wrap(yaw - self.int_yaw0)
        now = time.monotonic()
        if self.int_last_t is not None:
            dt = now - self.int_last_t
            if 0.0 < dt < 0.5:
                if abs(vel[0]) > 4.0 or abs(vel[1]) > 4.0:     # impossible for this robot: bad sample
                    self.int_last_t = now
                    return
                vx = 0.0 if abs(vel[0]) < self.vel_deadband else vel[0]
                vy = 0.0 if abs(vel[1]) < self.vel_deadband else vel[1]
                c, s = math.cos(yaw_rel), math.sin(yaw_rel)
                self.int_x += (c * vx - s * vy) * dt
                self.int_y += (s * vx + c * vy) * dt
        self.int_last_t = now
        self.int_quat = _rpy_to_quat(roll, pitch, yaw_rel)

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
