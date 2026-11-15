"""Message conversion helpers and QoS profiles for the follow node."""

import json

import numpy as np
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from vision_msgs.msg import Detection2D

IMAGE_RELIABILITY = ('reliable', 'best_effort')
# newest-only: a backlog behind a slow re-ID embed would just be dropped as stale anyway
DETECTIONS_QOS = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                            history=HistoryPolicy.KEEP_LAST, depth=1)
OUT_QOS = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                     history=HistoryPolicy.KEEP_LAST, depth=10)
ANNOTATED_QOS = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                           history=HistoryPolicy.KEEP_LAST, depth=1)


def image_qos(reliability: str) -> QoSProfile:
    """Image QoS; RELIABLE by default because best-effort loses full-size frames here."""
    rel = (ReliabilityPolicy.BEST_EFFORT if reliability == 'best_effort'
           else ReliabilityPolicy.RELIABLE)
    return QoSProfile(reliability=rel, history=HistoryPolicy.KEEP_LAST, depth=5)


def stamp_ns(stamp) -> int:
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def state_json(state, target_id, snap, embedder, event) -> str:
    return json.dumps({
        'state': state, 'target_id': target_id, 'similarity': snap['similarity'],
        'n_people': snap['n_people'], 'unpaired': snap['unpaired'], 'event': event,
        'reason': snap['reason'], 'embedder': embedder})


def event_text(out, target_id) -> str:
    """Human-readable log line for a lock event (no image data, ever)."""
    if out.event in ('locked', 'reacquired'):
        text = f'{out.event} target #{target_id}'
    else:
        text = out.event
        if out.reason and out.reason != out.event:
            text += f' ({out.reason})'
    if out.similarity is not None and out.event in ('reacquired', 'lost'):
        text += f' sim={out.similarity:.3f}'  # the value that decided it, never throttled
    return text


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
