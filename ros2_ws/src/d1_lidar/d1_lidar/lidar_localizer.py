"""Live lidar localization for the D1: publishes TF map->odom from the MetaCam's live odometry.

Inputs
  /metacam/odom          nav_msgs/Odometry   pose of the scanner body in its own frame `mapping_init` (relay, ~10 Hz)
  TF odom->base_link     from the robot bridge (body velocity + IMU yaw: smooth but drifting)
  /initialpose           geometry_msgs/PoseWithCovarianceStamped   "the robot really stands here" (dashboard / RViz)
Output
  TF map->odom (20 Hz), /lidar_localization/status (std_msgs/String, JSON, 2 Hz)

How it works (all planar x, y, yaw):
  * Placing the robot at P (pose of base_link in `map`) calibrates T_map_mi, the pose of the scanner's session origin in the map:
        T_map_mi = P * T_base_body * inv(T_mi_body_now)
    (T_base_body = where the scanner sits on the robot, parameters lidar_x/y/yaw).
  * From then on, every lidar sample gives the robot pose in the map:  T_map_base = T_map_mi * T_mi_body * inv(T_base_body)
    and  map->odom = T_map_base * inv(T_odom_base)   so odometry drift is removed continuously.
  * Without fresh lidar data the last map->odom is held (the robot keeps moving by odometry, drifting) and the status says so.
Status states: need_pose | localized | odometry_only | lidar_lost.  `ready` is true for localized and (degraded) odometry_only.
"""
import json
import math
import time

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped, TransformStamped
from nav_msgs.msg import Odometry
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from std_msgs.msg import String
from tf2_ros import Buffer, TransformBroadcaster, TransformListener

from d1_lidar import pose2d as P2


class LidarLocalizer(Node):
    def __init__(self):
        super().__init__('lidar_localizer')
        p = self.declare_parameter
        p('map_frame', 'map')
        p('odom_frame', 'odom')
        p('base_frame', 'base_link')
        p('lidar_topic', '/metacam/odom')
        # where the scanner body sits on the robot, in base_link (m, rad). PLACEHOLDER until measured.
        p('lidar_x', 0.0)
        p('lidar_y', 0.0)
        p('lidar_yaw', 0.0)
        p('x', float('nan'))             # optional start pose in the map (applied once the odom TF exists), e.g. a saved start pose
        p('y', float('nan'))
        p('yaw', 0.0)
        p('lidar_timeout_s', 1.0)       # no sample for this long -> lidar_lost (map->odom is held)
        p('smooth', 0.5)                # 1 = follow every lidar sample, lower = smoother map->odom
        p('jump_gate_m', 1.0)           # a lidar sample that moves the correction by more than this is rejected as a glitch ...
        p('jump_gate_deg', 40.0)
        p('jump_accept_s', 3.0)         # ... unless it persists this long (then the lidar is right, e.g. after a relocation)
        g = lambda n: self.get_parameter(n).value  # noqa: E731
        self.map_f, self.odom_f, self.base_f = g('map_frame'), g('odom_frame'), g('base_frame')
        self.timeout, self.smooth = float(g('lidar_timeout_s')), float(g('smooth'))
        self.gate_m, self.gate_a, self.accept_s = float(g('jump_gate_m')), math.radians(float(g('jump_gate_deg'))), float(g('jump_accept_s'))
        self.T_base_body = (float(g('lidar_x')), float(g('lidar_y')), float(g('lidar_yaw')))

        self.buf = Buffer()
        TransformListener(self.buf, self)
        self.br = TransformBroadcaster(self)
        self.L = None                   # latest T_mi_body (x, y, yaw)
        self.L_t = 0.0                  # monotonic time of the latest lidar sample
        self.n_lidar = 0
        self.T_map_mi = None            # calibrated by a placement while the lidar is live
        self.T_map_odom = (0.0, 0.0, 0.0)
        self.placed = False
        self.source = 'none'
        self.rejected = 0
        self.jump_since = None
        self.last_msg = 'place the robot on the map (Place robot)'
        self.create_subscription(Odometry, g('lidar_topic'), self.on_lidar,
                                 QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=5, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.create_subscription(PoseWithCovarianceStamped, '/initialpose', self.on_place, 10)
        self.pub_status = self.create_publisher(String, '/lidar_localization/status', 10)
        x, y = float(g('x')), float(g('y'))
        self.pending = None if (math.isnan(x) or math.isnan(y)) else (x, y, float(g('yaw')))
        self.create_timer(0.05, self.publish_tf)
        self.create_timer(0.5, self.publish_status)
        self.get_logger().info(f'lidar localizer up; scanner on robot at {self.T_base_body} (placeholder until measured); waiting for a pose')

    # ------------------------------------------------------------------ helpers
    def lidar_fresh(self):
        return self.L is not None and (time.monotonic() - self.L_t) < self.timeout

    def T_odom_base(self):
        try:
            t = self.buf.lookup_transform(self.odom_f, self.base_f, Time(), Duration(seconds=0.2)).transform
        except Exception:  # noqa: BLE001
            return None
        return (t.translation.x, t.translation.y, P2.from_quat(t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w))

    # ------------------------------------------------------------------ inputs
    def on_lidar(self, m):
        q, t = m.pose.pose.orientation, m.pose.pose.position
        self.L = (t.x, t.y, P2.from_quat(q.x, q.y, q.z, q.w))
        self.L_t = time.monotonic()
        self.n_lidar += 1
        if self.T_map_mi is None:
            return
        ob = self.T_odom_base()
        if ob is None:
            return
        T_map_base = P2.mul(P2.mul(self.T_map_mi, self.L), P2.inv(self.T_base_body))
        new = P2.mul(T_map_base, P2.inv(ob))
        d = P2.mul(P2.inv(self.T_map_odom), new)                    # how far this sample moves the correction
        if math.hypot(d[0], d[1]) > self.gate_m or abs(d[2]) > self.gate_a:
            now = time.monotonic()
            self.jump_since = self.jump_since or now
            if now - self.jump_since < self.accept_s:
                self.rejected += 1
                self.last_msg = f'lidar jumped {math.hypot(d[0], d[1]):.2f} m / {math.degrees(d[2]):.0f} deg, ignoring (glitch?)'
                return
            self.get_logger().warn('lidar pose stayed off for %.0f s: accepting it' % self.accept_s)
        self.jump_since = None
        self.T_map_odom = P2.blend(self.T_map_odom, new, self.smooth) if self.source == 'lidar' else new
        self.source = 'lidar'
        self.last_msg = 'localized by the lidar'

    def on_place(self, m):
        q, t = m.pose.pose.orientation, m.pose.pose.position
        self.place((t.x, t.y, P2.from_quat(q.x, q.y, q.z, q.w)))

    def place(self, Pm):
        """The operator says where the robot is: (re)calibrate the lidar alignment and/or the odometry-only correction."""
        ob = self.T_odom_base()
        if ob is None:
            self.get_logger().warn('no odom->base TF yet; cannot place', throttle_duration_sec=5.0)
            return False
        self.T_map_odom = P2.mul(Pm, P2.inv(ob))
        self.placed, self.jump_since = True, None
        if self.lidar_fresh():
            self.T_map_mi = P2.mul(P2.mul(Pm, self.T_base_body), P2.inv(self.L))
            self.source = 'lidar'
            self.last_msg = 'localized by the lidar'
            self.get_logger().info('placed at (%.2f, %.2f, %.0f deg); lidar alignment calibrated' % (Pm[0], Pm[1], math.degrees(Pm[2])))
        else:
            self.T_map_mi, self.source = None, 'odometry'
            self.last_msg = 'placed, but no live lidar data: navigating by odometry only (it drifts)'
            self.get_logger().warn('placed at (%.2f, %.2f, %.0f deg) WITHOUT lidar: odometry only' % (Pm[0], Pm[1], math.degrees(Pm[2])))
        return True

    # ------------------------------------------------------------------ outputs
    def state(self):
        if not self.placed:
            return 'need_pose'
        if self.T_map_mi is None:
            return 'odometry_only'
        return 'localized' if self.lidar_fresh() else 'lidar_lost'

    def publish_tf(self):
        if self.pending and self.place(self.pending):
            self.pending = None
        if self.T_map_mi is not None and not self.lidar_fresh() and self.source == 'lidar':
            self.source = 'odometry'                       # hold the last correction; the robot keeps going by odometry
            self.last_msg = 'lidar data lost: holding the last position, navigating by odometry (it drifts)'
        x, y, yaw = self.T_map_odom
        t = TransformStamped()
        t.header.stamp = (self.get_clock().now() + Duration(seconds=0.1)).to_msg()
        t.header.frame_id, t.child_frame_id = self.map_f, self.odom_f
        t.transform.translation.x, t.transform.translation.y = x, y
        t.transform.rotation.z, t.transform.rotation.w = math.sin(yaw / 2), math.cos(yaw / 2)
        self.br.sendTransform(t)

    def publish_status(self):
        st = self.state()
        s = {'state': st, 'ready': st in ('localized', 'odometry_only', 'lidar_lost'), 'source': self.source,
             'lidar_live': self.lidar_fresh(), 'lidar_age_s': None if self.L is None else round(time.monotonic() - self.L_t, 2),
             'lidar_samples': self.n_lidar, 'rejected': self.rejected, 'message': self.last_msg}
        if st == 'need_pose':
            s['message'] = ('lidar is live; ' if self.lidar_fresh() else 'no lidar data yet; ') + 'place the robot on the map'
        self.pub_status.publish(String(data=json.dumps(s)))


def main():
    rclpy.init()
    n = LidarLocalizer()
    try:
        rclpy.spin(n)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
