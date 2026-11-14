"""rclpy helpers shared by node-level tests (skipped when ROS is not sourced)."""

import time

import numpy as np
import pytest

rclpy = pytest.importorskip('rclpy')
pytest.importorskip('vision_msgs')

from rclpy.executors import SingleThreadedExecutor  # noqa: E402
from sensor_msgs.msg import Image  # noqa: E402

from human_perception.image_codec import fill_image_msg  # noqa: E402


def spin_until(executor, predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        executor.spin_once(timeout_sec=0.02)
        if predicate():
            return True
    return False


def make_image(sec=123, nanosec=456, frame_id='test_cam', h=48, w=64):
    msg = Image()
    msg.header.stamp.sec = sec
    msg.header.stamp.nanosec = nanosec
    msg.header.frame_id = frame_id
    return fill_image_msg(msg, np.zeros((h, w, 3), np.uint8))


def new_executor(*nodes):
    ex = SingleThreadedExecutor()
    for n in nodes:
        ex.add_node(n)
    return ex
