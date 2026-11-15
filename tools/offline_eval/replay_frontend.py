"""Replay front-end for offline mapping experiments (run on its own ROS_DOMAIN_ID).

Bag topics are replayed with *_rec names. This node republishes:
  /camera/image_raw, /camera/camera_info  with header stamps shifted back by OFF (video latency)
  /imu                                    (frame imu_link)
  /odom + TF odom->base_link, odom->base_footprint:
     mode 'raw'       -> the recorded odometry/TF (SDK heading as recorded)
     mode 'corrected' -> rebuilt from the recorded IMU + body velocity with the heading-bias correction (HeadingBias)
Usage: replay_frontend.py <raw|corrected> <offset_s>
"""
import math
import sys

sys.path.insert(0, '/home/sparc/SafetyAI/ros2_ws/src/agibot_d1_bridge/agibot_d1_bridge')
import rclpy  # noqa: E402
from geometry_msgs.msg import TransformStamped  # noqa: E402
from heading_bias import HeadingBias  # noqa: E402
from nav_msgs.msg import Odometry  # noqa: E402
from rclpy.node import Node  # noqa: E402
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy  # noqa: E402
from sensor_msgs.msg import CameraInfo, Image, Imu  # noqa: E402
from tf2_msgs.msg import TFMessage  # noqa: E402
from tf2_ros import TransformBroadcaster  # noqa: E402

MODE = sys.argv[1]
OFF = float(sys.argv[2])


def rpy(w, x, y, z):
    return (math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)), math.asin(max(-1, min(1, 2 * (w * y - z * x)))),
            math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))


def quat(r, p, y):
    cr, sr, cp, sp, cy, sy = math.cos(r / 2), math.sin(r / 2), math.cos(p / 2), math.sin(p / 2), math.cos(y / 2), math.sin(y / 2)
    return (cr * cp * cy + sr * sp * sy, sr * cp * cy - cr * sp * sy, cr * sp * cy + sr * cp * sy, cr * cp * sy - sr * sp * cy)


class Front(Node):
    def __init__(self):
        super().__init__('replay_frontend')
        be = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=2, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.pi = self.create_publisher(Image, '/camera/image_raw', be)
        self.pc = self.create_publisher(CameraInfo, '/camera/camera_info', be)
        self.pimu = self.create_publisher(Imu, '/imu', 10)
        self.podom = self.create_publisher(Odometry, '/odom', 10)
        self.tfb = TransformBroadcaster(self)
        self.create_subscription(Image, '/camera/image_raw_rec', lambda m: self.pi.publish(self.shift(m)), be)
        self.create_subscription(CameraInfo, '/camera/camera_info_rec', lambda m: self.pc.publish(self.shift(m)), be)
        self.create_subscription(Imu, '/imu_rec', self.on_imu, 50)
        self.create_subscription(Odometry, '/odom_rec', self.on_odom, 50)
        self.create_subscription(TFMessage, '/tf_rec', self.on_tf, 100)
        self.hb = HeadingBias()
        self.vel = (0.0, 0.0)
        self.z = 0.15
        self.x = self.y = 0.0
        self.last_t = None
        self.yaw0 = None
        self.last_imu = None
        self.get_logger().info(f'mode={MODE} offset={OFF}')

    def shift(self, m):
        t = m.header.stamp.sec * 1_000_000_000 + m.header.stamp.nanosec - int(OFF * 1e9)
        m.header.stamp.sec, m.header.stamp.nanosec = divmod(max(t, 0), 1_000_000_000)
        return m

    def on_tf(self, m):
        if MODE != 'raw':
            return          # corrected mode publishes its own odom->base_* ; recorded map->odom is never wanted
        keep = [t for t in m.transforms if t.header.frame_id == 'odom' and t.child_frame_id in ('base_link', 'base_footprint')]
        if keep:
            self.tfb.sendTransform(keep)

    def on_odom(self, m):
        self.vel = (m.twist.twist.linear.x, m.twist.twist.linear.y)
        self.z = m.pose.pose.position.z
        if MODE == 'raw':
            self.podom.publish(m)

    def on_imu(self, m):
        m.header.frame_id = 'imu_link'
        self.pimu.publish(m)
        if MODE != 'corrected':
            return
        t = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
        q = m.orientation
        roll, pitch, yaw_raw = rpy(q.w, q.x, q.y, q.z)
        if self.yaw0 is None:
            self.yaw0 = yaw_raw
        speed = math.hypot(*self.vel)
        yaw = self.hb.update(t, yaw_raw, m.angular_velocity.z, speed) - self.yaw0
        if self.last_t is not None and 0 < t - self.last_t < 0.5:
            vx = 0.0 if abs(self.vel[0]) < 0.02 else self.vel[0]
            vy = 0.0 if abs(self.vel[1]) < 0.02 else self.vel[1]
            c, s = math.cos(yaw), math.sin(yaw)
            self.x += (c * vx - s * vy) * (t - self.last_t)
            self.y += (s * vx + c * vy) * (t - self.last_t)
        self.last_t = t
        qw, qx, qy, qz = quat(roll, pitch, yaw)
        stamp = m.header.stamp
        out = []
        for child, zz, (rw, rx, ry, rz) in (('base_link', self.z, (qw, qx, qy, qz)), ('base_footprint', 0.0, quat(0, 0, yaw))):
            tf = TransformStamped()
            tf.header.stamp, tf.header.frame_id, tf.child_frame_id = stamp, 'odom', child
            tf.transform.translation.x, tf.transform.translation.y, tf.transform.translation.z = self.x, self.y, zz
            tf.transform.rotation.w, tf.transform.rotation.x, tf.transform.rotation.y, tf.transform.rotation.z = rw, rx, ry, rz
            out.append(tf)
        self.tfb.sendTransform(out)
        o = Odometry()
        o.header.stamp, o.header.frame_id, o.child_frame_id = stamp, 'odom', 'base_link'
        o.pose.pose.position.x, o.pose.pose.position.y, o.pose.pose.position.z = self.x, self.y, self.z
        o.pose.pose.orientation.w, o.pose.pose.orientation.x, o.pose.pose.orientation.y, o.pose.pose.orientation.z = qw, qx, qy, qz
        o.twist.twist.linear.x, o.twist.twist.linear.y = self.vel
        self.podom.publish(o)


rclpy.init()
rclpy.spin(Front())
