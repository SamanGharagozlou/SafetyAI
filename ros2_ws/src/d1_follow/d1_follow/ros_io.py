"""Message conversion helpers and QoS profiles for the follow node."""

import numpy as np
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from vision_msgs.msg import Detection2D

IMAGE_QOS = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                       history=HistoryPolicy.KEEP_LAST, depth=5)
DETECTIONS_QOS = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                            history=HistoryPolicy.KEEP_LAST, depth=10)
OUT_QOS = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                     history=HistoryPolicy.KEEP_LAST, depth=10)
ANNOTATED_QOS = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                           history=HistoryPolicy.KEEP_LAST, depth=1)


def stamp_ns(stamp) -> int:
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def detections_to_arrays(msg):
    """Detection2DArray -> (boxes Nx4 xyxy float32, scores N float32)."""
    boxes, scores = [], []
    for d in msg.detections:
        b = d.bbox
        cx, cy = b.center.position.x, b.center.position.y
        hw, hh = b.size_x / 2, b.size_y / 2
        boxes.append([cx - hw, cy - hh, cx + hw, cy + hh])
        scores.append(d.results[0].hypothesis.score if d.results else 0.0)
    return np.array(boxes, np.float32).reshape(-1, 4), np.array(scores, np.float32)


def target_msg(header, track) -> Detection2D:
    x1, y1, x2, y2 = (float(v) for v in track.box)
    det = Detection2D()
    det.header = header
    det.id = str(track.id)
    det.bbox.center.position.x, det.bbox.center.position.y = (x1 + x2) / 2, (y1 + y2) / 2
    det.bbox.size_x, det.bbox.size_y = x2 - x1, y2 - y1
    return det
