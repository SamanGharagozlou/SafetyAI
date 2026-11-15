"""In-process ROS test: stamp pairing, the unpaired path and one full lock cycle."""

import json
import time

import numpy as np
import pytest

rclpy = pytest.importorskip('rclpy')
pytest.importorskip('human_perception.image_codec')

from geometry_msgs.msg import Twist                                          # noqa: E402
from human_perception.image_codec import fill_image_msg                      # noqa: E402
from rclpy.executors import MultiThreadedExecutor                            # noqa: E402
from rclpy.node import Node                                                  # noqa: E402
from sensor_msgs.msg import Image                                            # noqa: E402
from std_msgs.msg import String                                              # noqa: E402
from vision_msgs.msg import (Detection2D, Detection2DArray,                  # noqa: E402
                             ObjectHypothesisWithPose)

from d1_follow.ros_io import DETECTIONS_QOS, OUT_QOS, image_qos              # noqa: E402
from d1_follow.target_follower_node import TargetFollower                    # noqa: E402

W, H = 640, 480
BOX = (300.0, 120.0, 420.0, 420.0)   # one person, slightly right of centre
IMAGE_TOPIC = '/follow_test/image'
DET_TOPIC = '/follow_test/detections'


def _frame():
    frame = np.full((H, W, 3), 40, np.uint8)
    x1, y1, x2, y2 = (int(v) for v in BOX)
    frame[y1:y2, x1:x2] = (30, 200, 60)   # a solid 'person' the histogram embedder can match
    return frame


class Harness(Node):
    """Publishes the synthetic camera + detector stream and records the node's outputs."""

    def __init__(self):
        super().__init__('follow_test_harness')
        self._img_pub = self.create_publisher(Image, IMAGE_TOPIC, image_qos('reliable'))
        self._det_pub = self.create_publisher(Detection2DArray, DET_TOPIC, DETECTIONS_QOS)
        self.states, self.cmds, self.targets = [], [], []
        self.create_subscription(String, '/follow/state',
                                 lambda m: self.states.append(json.loads(m.data)), OUT_QOS)
        self.create_subscription(Twist, '/follow/cmd_vel', self.cmds.append, OUT_QOS)
        self.create_subscription(Detection2D, '/follow/target', self.targets.append, OUT_QOS)
        self._frame = _frame()

    def stamp(self):
        return self.get_clock().now().to_msg()

    def publish_image(self, stamp):
        msg = fill_image_msg(Image(), self._frame)
        msg.header.stamp, msg.header.frame_id = stamp, 'camera'
        self._img_pub.publish(msg)

    def publish_detection(self, stamp):
        msg = Detection2DArray()
        msg.header.stamp, msg.header.frame_id = stamp, 'camera'
        det = Detection2D()
        det.header = msg.header
        x1, y1, x2, y2 = BOX
        det.bbox.center.position.x, det.bbox.center.position.y = (x1 + x2) / 2, (y1 + y2) / 2
        det.bbox.size_x, det.bbox.size_y = x2 - x1, y2 - y1
        hyp = ObjectHypothesisWithPose()
        hyp.hypothesis.class_id, hyp.hypothesis.score = 'person', 0.9
        det.results.append(hyp)
        msg.detections.append(det)
        self._det_pub.publish(msg)


def _spin(executor, seconds):
    end = time.time() + seconds
    while time.time() < end:
        executor.spin_once(timeout_sec=0.01)


def test_follow_node_counts_unpaired_then_locks_on():
    rclpy.init(args=['--ros-args',
                     '-p', f'image_topic:={IMAGE_TOPIC}',
                     '-p', f'detections_topic:={DET_TOPIC}',
                     '-p', 'embedder:=histogram',
                     '-p', 'auto_lock:=true'])
    node, harness = None, None
    try:
        node = TargetFollower()
        harness = Harness()
        executor = MultiThreadedExecutor(num_threads=4)
        executor.add_node(node)
        executor.add_node(harness)
        _spin(executor, 0.3)   # discovery

        # detections before any image: must not crash, must be counted
        harness.publish_detection(harness.stamp())
        _spin(executor, 0.6)
        assert harness.states and harness.states[-1]['unpaired'] >= 1

        deadline = time.time() + 5.0
        while time.time() < deadline:
            stamp = harness.stamp()
            harness.publish_image(stamp)
            _spin(executor, 0.03)      # the detector always lags its frame
            harness.publish_detection(stamp)
            _spin(executor, 0.07)
            if harness.targets and any(s['state'] == 'TRACKING' for s in harness.states):
                break
        _spin(executor, 0.6)

        assert any(s['state'] == 'TRACKING' for s in harness.states)
        assert harness.targets
        assert harness.states[-1]['embedder'] == 'histogram'
        assert harness.cmds
        assert all(abs(c.angular.z) <= 0.8 and c.linear.x >= 0.0 for c in harness.cmds)
        assert any(c.angular.z != 0.0 for c in harness.cmds)   # the controller did run
    finally:
        for n in (node, harness):
            if n is not None:
                n.destroy_node()
        rclpy.shutdown()
