#!/usr/bin/env python3
"""Fake MetaCam Lite for testing d1_lidar without hardware: publishes /metacam/odom (frame mapping_init, child body, 10 Hz) from the
simulator's TRUE pose (/sim/truth, published by tools/sim_robot.py).

  T_mi_body = inv(T_map_mi) * T_map_base_true * T_base_body
The scanner session origin (T_map_mi, default (-3, 2, 1.0 rad)) is deliberately NOT the map origin, and the scanner sits on the robot
at --body x y yaw. --noise adds Gaussian position noise (m). --dropout s makes it stop publishing for 5 s after s seconds.
"""
import argparse
import math
import random
import time

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node


def mul(a, b):
    c, s = math.cos(a[2]), math.sin(a[2])
    return (a[0] + c * b[0] - s * b[1], a[1] + s * b[0] + c * b[1], a[2] + b[2])


def inv(a):
    c, s = math.cos(a[2]), math.sin(a[2])
    return (-(c * a[0] + s * a[1]), -(-s * a[0] + c * a[1]), -a[2])


class Fake(Node):
    def __init__(self, a):
        super().__init__('fake_metacam')
        self.T_map_mi, self.T_base_body, self.noise, self.dropout = tuple(a.map_mi), tuple(a.body), a.noise, a.dropout
        self.t0, self.truth = time.monotonic(), None
        self.pub = self.create_publisher(Odometry, '/metacam/odom', 10)
        self.create_subscription(Odometry, '/sim/truth', self.on_truth, 10)
        self.create_timer(0.1, self.tick)

    def on_truth(self, m):
        q = m.pose.pose.orientation
        self.truth = (m.pose.pose.position.x, m.pose.pose.position.y, 2 * math.atan2(q.z, q.w))

    def tick(self):
        if self.truth is None:
            return
        el = time.monotonic() - self.t0
        if self.dropout > 0 and self.dropout < el < self.dropout + 5.0:
            return
        L = mul(mul(inv(self.T_map_mi), self.truth), self.T_base_body)
        o = Odometry()
        o.header.stamp, o.header.frame_id, o.child_frame_id = self.get_clock().now().to_msg(), 'mapping_init', 'body'
        o.pose.pose.position.x = L[0] + random.gauss(0, self.noise)
        o.pose.pose.position.y = L[1] + random.gauss(0, self.noise)
        o.pose.pose.orientation.z, o.pose.pose.orientation.w = math.sin(L[2] / 2), math.cos(L[2] / 2)
        self.pub.publish(o)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--map-mi', type=float, nargs=3, default=[-3.0, 2.0, 1.0])
    ap.add_argument('--body', type=float, nargs=3, default=[0.0, 0.0, 0.0])
    ap.add_argument('--noise', type=float, default=0.01)
    ap.add_argument('--dropout', type=float, default=0.0)
    a, ros = ap.parse_known_args()
    rclpy.init(args=ros)
    try:
        rclpy.spin(Fake(a))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
