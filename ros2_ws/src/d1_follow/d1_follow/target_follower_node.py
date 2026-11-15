"""
ROS 2 node: lock onto one guest and publish a DRY-RUN follow velocity.

Consumes ``/camera/image_raw`` + ``/perception/humans/detections`` and publishes
``/follow/cmd_vel`` (never ``/cmd_vel``), ``/follow/state`` (JSON),
``/follow/target`` and ``/follow/annotated_image`` (only while subscribed).
Services ``/d1_follow/lock`` and ``/d1_follow/release`` (std_srvs/Trigger).
"""

import dataclasses
import json
import sys
import threading
from collections import OrderedDict

import numpy as np
import rclpy
from geometry_msgs.msg import Twist
from human_perception.image_codec import fill_image_msg, image_msg_to_bgr
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import String
from std_srvs.srv import Trigger
from vision_msgs.msg import Detection2D, Detection2DArray

from . import annotate
from .embedder import make_embedder
from .follow_controller import FollowController, FollowParams
from .target_lock import LockParams, State, TargetLock
from .tracker import IouTracker

# global safety caps, applied no matter what the parameters say
VX_CAP = 0.5
WZ_CAP = 0.8
EMBED_LOG_PERIOD_S = 10.0

IMAGE_QOS = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                       history=HistoryPolicy.KEEP_LAST, depth=5)
DETECTIONS_QOS = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                            history=HistoryPolicy.KEEP_LAST, depth=10)
OUT_QOS = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                     history=HistoryPolicy.KEEP_LAST, depth=10)
ANNOTATED_QOS = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                           history=HistoryPolicy.KEEP_LAST, depth=1)


def _stamp_ns(stamp) -> int:
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


class TargetFollower(Node):
    def __init__(self) -> None:
        super().__init__('d1_follow')
        log = self.get_logger()

        self._lock_p = self._declare_dataclass(LockParams)
        self._follow_p = self._declare_dataclass(FollowParams)
        self._tracker = IouTracker(
            iou_min=self.declare_parameter('iou_min', 0.3).value,
            min_hits=self.declare_parameter('min_hits', 3).value,
            max_misses=self.declare_parameter('max_misses', 15).value)
        image_topic = self.declare_parameter('image_topic', '/camera/image_raw').value
        det_topic = self.declare_parameter(
            'detections_topic', '/perception/humans/detections').value
        kind = self.declare_parameter('embedder', 'osnet').value
        device = self.declare_parameter('device', 'cpu').value
        auto_lock = self.declare_parameter('auto_lock', False).value
        self._stale_s = self.declare_parameter('stale_s', 0.5).value
        self._image_buffer = max(1, self.declare_parameter('image_buffer', 30).value)

        if self._follow_p.max_vx > VX_CAP or self._follow_p.max_wz > WZ_CAP:
            log.warn(f'speed limits clamped to vx<={VX_CAP} m/s, wz<={WZ_CAP} rad/s')
        self._follow_p.max_vx = min(self._follow_p.max_vx, VX_CAP)
        self._follow_p.max_wz = min(self._follow_p.max_wz, WZ_CAP)
        self._controller = FollowController(self._follow_p)

        if kind not in ('osnet', 'histogram'):
            log.warn(f'unknown embedder {kind!r}; using histogram')
        self._embedder = make_embedder(kind, device, log=log.warn)
        self._lock = TargetLock(self._lock_p, auto_lock=auto_lock)

        self._mx = threading.Lock()  # guards self._lock (callbacks + services)
        self._lock_evt = threading.Event()
        self._lock_result = None
        self._images = OrderedDict()  # stamp_ns -> Image
        self._size = (0, 0)
        self._last_det = None
        self._embed_log_t = -1e9
        self._snap = {'similarity': None, 'n_people': 0, 'event': None, 'reason': None}

        cb = MutuallyExclusiveCallbackGroup()
        srv_cb = ReentrantCallbackGroup()
        self._pub_cmd = self.create_publisher(Twist, '/follow/cmd_vel', OUT_QOS)
        self._pub_state = self.create_publisher(String, '/follow/state', OUT_QOS)
        self._pub_target = self.create_publisher(Detection2D, '/follow/target', OUT_QOS)
        self._pub_annot = self.create_publisher(
            Image, '/follow/annotated_image', ANNOTATED_QOS)
        self.create_subscription(Image, image_topic, self._on_image, IMAGE_QOS,
                                 callback_group=cb)
        self.create_subscription(Detection2DArray, det_topic, self._on_detections,
                                 DETECTIONS_QOS, callback_group=cb)
        self.create_timer(0.1, self._on_stale_timer, callback_group=cb)
        self.create_timer(0.5, lambda: self._publish_state(None), callback_group=cb)
        self.create_service(Trigger, '/d1_follow/lock', self._srv_lock,
                            callback_group=srv_cb)
        self.create_service(Trigger, '/d1_follow/release', self._srv_release,
                            callback_group=srv_cb)
        log.info(f'ready: embedder={self._embedder.name} auto_lock={auto_lock} '
                 f'images={image_topic} detections={det_topic} (dry-run, /follow/cmd_vel)')

    def _declare_dataclass(self, cls):
        """Declare one parameter per dataclass field (default from the class)."""
        defaults = cls()
        return cls(**{f.name: self.declare_parameter(f.name, getattr(defaults, f.name)).value
                      for f in dataclasses.fields(cls)})

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    # -- inputs ---------------------------------------------------------------
    def _on_image(self, msg: Image) -> None:
        self._images[_stamp_ns(msg.header.stamp)] = msg
        while len(self._images) > self._image_buffer:
            self._images.popitem(last=False)

    def _on_detections(self, msg: Detection2DArray) -> None:
        now = self._now()
        boxes, scores = [], []
        for d in msg.detections:
            b = d.bbox
            cx, cy = b.center.position.x, b.center.position.y
            hw, hh = b.size_x / 2, b.size_y / 2
            boxes.append([cx - hw, cy - hh, cx + hw, cy + hh])
            scores.append(d.results[0].hypothesis.score if d.results else 0.0)
        tracks = self._tracker.update(np.array(boxes, np.float32).reshape(-1, 4),
                                      np.array(scores, np.float32))

        img_msg = self._images.get(_stamp_ns(msg.header.stamp))
        if img_msg is not None:
            self._size = (int(img_msg.width), int(img_msg.height))
        w, h = self._size
        frame = []  # decoded at most once per detection message

        def decode():
            if not frame:
                frame.append(image_msg_to_bgr(img_msg))
            return frame[0]

        def embed(ts):
            if img_msg is None:
                return None
            try:
                return self._embedder.embed(decode(), np.array([t.box for t in ts]))
            except Exception as exc:  # noqa: B902 - never let re-ID kill the node
                if now - self._embed_log_t > EMBED_LOG_PERIOD_S:
                    self._embed_log_t = now
                    self.get_logger().warn(f'embedding failed: {type(exc).__name__}: {exc}')
                return None

        with self._mx:
            out = self._lock.update(now, tracks, w, h, embed)
        self._last_det = now

        target = out.target if out.state == State.TRACKING else None
        # no frame size yet (no paired image so far): hold still
        vx, wz = (0.0, 0.0)
        if w and h:
            vx, wz = self._controller.compute(target.box if target else None, w, h)
        self._publish_cmd(vx, wz)
        if target is not None:
            self._publish_target(msg.header, target)

        self._snap = {
            'similarity': None if out.similarity is None else round(out.similarity, 3),
            'n_people': sum(1 for t in tracks if t.visible),
            'event': out.event, 'reason': out.reason}
        if out.event:
            self._log_event(out)
            if out.event in ('locked', 'lock_failed'):
                self._lock_result = (out.event, out.reason, target.id if target else None)
                self._lock_evt.set()
            self._publish_state(out.event)

        if img_msg is not None and self._pub_annot.get_subscription_count() > 0:
            try:
                ann = annotate.draw(decode(), tracks, out, vx, wz)
                res = fill_image_msg(Image(), ann)
                res.header = img_msg.header
                self._pub_annot.publish(res)
            except Exception as exc:  # noqa: B902
                self.get_logger().warn(f'annotation failed: {type(exc).__name__}',
                                       throttle_duration_sec=10.0)

    # -- outputs --------------------------------------------------------------
    def _publish_cmd(self, vx: float, wz: float) -> None:
        cmd = Twist()
        cmd.linear.x, cmd.angular.z = float(vx), float(wz)
        self._pub_cmd.publish(cmd)

    def _publish_target(self, header, track) -> None:
        x1, y1, x2, y2 = (float(v) for v in track.box)
        det = Detection2D()
        det.header = header
        det.id = str(track.id)
        det.bbox.center.position.x, det.bbox.center.position.y = (x1 + x2) / 2, (y1 + y2) / 2
        det.bbox.size_x, det.bbox.size_y = x2 - x1, y2 - y1
        self._pub_target.publish(det)

    def _publish_state(self, event) -> None:
        with self._mx:
            state, target_id = self._lock.state.value, self._lock.target_id
        s = self._snap
        self._pub_state.publish(String(data=json.dumps({
            'state': state, 'target_id': target_id, 'similarity': s['similarity'],
            'n_people': s['n_people'], 'event': event,
            'reason': s['reason'], 'embedder': self._embedder.name})))

    def _log_event(self, out) -> None:
        if out.event in ('locked', 'reacquired'):
            text = f'{out.event} target #{self._lock.target_id}'
        else:
            text = out.event
            if out.reason and out.reason != out.event:
                text += f' ({out.reason})'
        self.get_logger().info(text)

    def _on_stale_timer(self) -> None:
        # no fresh detections: never keep a stale velocity alive
        if self._last_det is None or self._now() - self._last_det > self._stale_s:
            self._publish_cmd(0.0, 0.0)

    # -- services -------------------------------------------------------------
    def _srv_lock(self, request, response):
        self._lock_evt.clear()
        self._lock_result = None
        with self._mx:
            self._lock.request_lock(self._now())
        if not self._lock_evt.wait(self._lock_p.lock_timeout_s + 1.0):
            response.success, response.message = False, 'timeout: no detections'
            return response
        event, reason, target_id = self._lock_result
        response.success = event == 'locked'
        response.message = f'locked target #{target_id}' if response.success else str(reason)
        return response

    def _srv_release(self, request, response):
        with self._mx:
            self._lock.release()
        response.success, response.message = True, 'released'
        return response


def main(args=None) -> int:
    rclpy.init(args=args)
    node = None
    try:
        node = TargetFollower()
        executor = MultiThreadedExecutor(num_threads=3)
        executor.add_node(node)
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
