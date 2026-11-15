#!/usr/bin/env python3
"""Fake D1 for testing d1_localization without the robot: publishes stamped /odom (driving forward 0.2 m/s) and /imu."""
import rclpy, math
from rclpy.node import Node
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu
rclpy.init(); n = Node('fake_d1'); po = n.create_publisher(Odometry,'odom',10); pi = n.create_publisher(Imu,'imu',10)
x = 0.0; dt = 0.05
def tick():
    global x; x += 0.2*dt; t = n.get_clock().now().to_msg()
    o = Odometry(); o.header.stamp=t; o.header.frame_id='odom'; o.child_frame_id='base_link'
    o.pose.pose.position.x=x; o.pose.pose.orientation.w=1.0; o.twist.twist.linear.x=0.2
    for i in (0,7,14,21,28,35): o.pose.covariance[i]=0.01; o.twist.covariance[i]=0.01
    po.publish(o)
    m = Imu(); m.header.stamp=t; m.header.frame_id='imu_link'; m.orientation.w=1.0
    for i in (0,4,8): m.orientation_covariance[i]=0.01; m.angular_velocity_covariance[i]=0.01; m.linear_acceleration_covariance[i]=0.01
    pi.publish(m)
n.create_timer(dt, tick); rclpy.spin(n)
