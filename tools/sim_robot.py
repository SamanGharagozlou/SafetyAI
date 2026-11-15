#!/usr/bin/env python3
"""Fake D1 bridge for developing the frontend / Nav2 without the robot (use a private ROS_DOMAIN_ID, never the live one).

Integrates /cmd_vel (vx, vy in the body frame, wz) and publishes what agibot_d1_bridge publishes: /odom, TF odom->base_link
(z = 0.375) and odom->base_footprint (z = 0), /ctrl_mode (1 standing, 18 while moving), /connected, /battery, /imu.
Services stand_up/lie_down (Trigger) under /d1_cmd_vel_bridge/ and /robot_cmd 'stand_up|lie_down|damp' change the mode.

  python3 tools/sim_robot.py [--x 0 --y 0 --yaw 0] [--drift 0.0]     (--drift: yaw error in deg per second, to test re-placing)
"""
import argparse
import math
import time

import rclpy
from geometry_msgs.msg import Twist, TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import BatteryState, Imu
from std_msgs.msg import Bool, Int32, String
from std_srvs.srv import Trigger
from tf2_ros import TransformBroadcaster


class Sim(Node):
    def __init__(self, a):
        super().__init__('d1_cmd_vel_bridge')            # same node name as the real bridge so service paths match
        self.x, self.y, self.yaw, self.drift = a.x, a.y, a.yaw, math.radians(a.drift)
        self.mode, self.cmd, self.t_cmd = 1, (0.0, 0.0, 0.0), 0.0
        self.tf = TransformBroadcaster(self)
        self.po = self.create_publisher(Odometry, 'odom', 10)
        self.pm = self.create_publisher(Int32, 'ctrl_mode', 10)
        self.pc = self.create_publisher(Bool, 'connected', 10)
        self.pb = self.create_publisher(BatteryState, 'battery', 10)
        self.pi = self.create_publisher(Imu, 'imu', 10)
        self.create_subscription(Twist, 'cmd_vel', self.on_cmd, 10)
        self.create_subscription(String, 'robot_cmd', lambda m: self.do(m.data), 10)
        for s in ('stand_up', 'lie_down', 'damp', 'shake_hand'):
            self.create_service(Trigger, '~/' + s, lambda q, r, s=s: self.srv(r, s))
        self.last = time.monotonic()
        self.create_timer(0.02, self.step)
        self.create_timer(0.1, self.slow)

    def do(self, c):
        c = c.strip().lower()
        self.mode = {'stand_up': 1, 'stand': 1, 'lie_down': 51, 'lie': 51, 'damp': 0, 'passive': 0}.get(c, self.mode)

    def srv(self, r, s):
        self.do(s)
        r.success, r.message = True, f'{s} -> 0 (sim)'
        return r

    def on_cmd(self, m):
        if self.mode in (1, 18):
            self.cmd, self.t_cmd = (m.linear.x, m.linear.y, m.angular.z), time.monotonic()

    def step(self):
        now = time.monotonic()
        dt, self.last = now - self.last, now
        moving = now - self.t_cmd < 0.5 and any(abs(v) > 1e-3 for v in self.cmd)
        vx, vy, wz = self.cmd if moving else (0.0, 0.0, 0.0)
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        self.x += (c * vx - s * vy) * dt
        self.y += (s * vx + c * vy) * dt
        self.yaw += (wz + self.drift) * dt
        if self.mode in (1, 18):
            self.mode = 18 if moving else 1
        st = self.get_clock().now().to_msg()
        for child, z in (('base_link', 0.375), ('base_footprint', 0.0)):
            t = TransformStamped()
            t.header.stamp, t.header.frame_id, t.child_frame_id = st, 'odom', child
            t.transform.translation.x, t.transform.translation.y, t.transform.translation.z = self.x, self.y, z
            t.transform.rotation.z, t.transform.rotation.w = math.sin(self.yaw / 2), math.cos(self.yaw / 2)
            self.tf.sendTransform(t)
        o = Odometry()
        o.header.stamp, o.header.frame_id, o.child_frame_id = st, 'odom', 'base_link'
        o.pose.pose.position.x, o.pose.pose.position.y, o.pose.pose.position.z = self.x, self.y, 0.375
        o.pose.pose.orientation.z, o.pose.pose.orientation.w = math.sin(self.yaw / 2), math.cos(self.yaw / 2)
        o.twist.twist.linear.x, o.twist.twist.linear.y, o.twist.twist.angular.z = vx, vy, wz
        self.po.publish(o)

    def slow(self):
        self.pm.publish(Int32(data=self.mode))
        self.pc.publish(Bool(data=True))
        b = BatteryState()
        b.percentage = 0.9
        self.pb.publish(b)
        m = Imu()
        m.header.stamp, m.header.frame_id = self.get_clock().now().to_msg(), 'imu_link'
        m.orientation.z, m.orientation.w = math.sin(self.yaw / 2), math.cos(self.yaw / 2)
        self.pi.publish(m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--x', type=float, default=0.0)
    ap.add_argument('--y', type=float, default=0.0)
    ap.add_argument('--yaw', type=float, default=0.0)
    ap.add_argument('--drift', type=float, default=0.0)
    a, ros = ap.parse_known_args()
    rclpy.init(args=ros)
    try:
        rclpy.spin(Sim(a))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
