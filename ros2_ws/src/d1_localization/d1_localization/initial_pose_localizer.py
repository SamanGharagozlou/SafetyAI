#!/usr/bin/env python3
"""Hand-placed localisation: you tell the robot where it is, odometry does the rest.

On every /initialpose (RViz "2D Pose Estimate") or on the x/y/yaw parameters it computes
    map->odom = (map->base_link you placed) * inverse(odom->base_link right now)
and keeps broadcasting it. The robot then moves in the map by odometry (drifts, no correction).
Re-place it any time to cancel drift.
"""
import math, rclpy
from rclpy.node import Node
from rclpy.duration import Duration
from rclpy.time import Time
from geometry_msgs.msg import PoseWithCovarianceStamped, TransformStamped
from tf2_ros import Buffer, TransformListener, TransformBroadcaster


def yaw_of(q): return math.atan2(2*(q.w*q.z + q.x*q.y), 1 - 2*(q.y*q.y + q.z*q.z))


class Localizer(Node):
    def __init__(self):
        super().__init__("initial_pose_localizer")
        self.base = self.declare_parameter("base_frame", "base_link").value
        self.odom = self.declare_parameter("odom_frame", "odom").value
        self.map = self.declare_parameter("map_frame", "map").value
        self.buf = Buffer(); TransformListener(self.buf, self)
        self.br = TransformBroadcaster(self)
        self.T = (0.0, 0.0, 0.0)  # map->odom (x, y, yaw); identity until a pose is given
        self.create_subscription(PoseWithCovarianceStamped, "/initialpose", self.on_pose, 10)
        self.create_timer(0.05, self.publish)
        x = self.declare_parameter("x", float("nan")).value
        y = self.declare_parameter("y", float("nan")).value
        yaw = self.declare_parameter("yaw", 0.0).value
        self.pending = None if math.isnan(x) or math.isnan(y) else (x, y, yaw)
        self.get_logger().info("waiting for pose: use RViz '2D Pose Estimate' (or x/y/yaw params)")

    def odom_to_base(self):
        t = self.buf.lookup_transform(self.odom, self.base, Time(), Duration(seconds=0.5)).transform
        return t.translation.x, t.translation.y, yaw_of(t.rotation)

    def apply(self, mx, my, myaw):
        try: ox, oy, oyaw = self.odom_to_base()
        except Exception as e:
            self.get_logger().warn(f"no {self.odom}->{self.base} TF yet ({e}); will retry"); return False
        yaw = myaw - oyaw                      # T_map_odom = T_map_base * T_odom_base^-1
        c, s = math.cos(yaw), math.sin(yaw)
        self.T = (mx - (c*ox - s*oy), my - (s*ox + c*oy), yaw)
        self.get_logger().info(f"placed at x={mx:.2f} y={my:.2f} yaw={math.degrees(myaw):.0f} deg")
        return True

    def on_pose(self, m):
        p = m.pose.pose
        self.pending = (p.position.x, p.position.y, yaw_of(p.orientation))

    def publish(self):
        if self.pending and self.apply(*self.pending): self.pending = None
        x, y, yaw = self.T
        t = TransformStamped()
        t.header.stamp = (self.get_clock().now() + Duration(seconds=0.1)).to_msg()
        t.header.frame_id, t.child_frame_id = self.map, self.odom
        t.transform.translation.x, t.transform.translation.y = x, y
        t.transform.rotation.z, t.transform.rotation.w = math.sin(yaw/2), math.cos(yaw/2)
        self.br.sendTransform(t)


def main():
    rclpy.init(); rclpy.spin(Localizer())

if __name__ == "__main__": main()
