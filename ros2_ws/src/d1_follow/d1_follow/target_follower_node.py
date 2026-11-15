"""
ROS 2 node: lock onto one guest and publish a DRY-RUN follow velocity.

Consumes ``/camera/image_raw`` + ``/perception/humans/detections`` and publishes
``/follow/cmd_vel`` (never ``/cmd_vel``), ``/follow/state`` (JSON),
``/follow/target`` and ``/follow/annotated_image`` (only while subscribed).
Services ``/d1_follow/lock`` and ``/d1_follow/release`` (std_srvs/Trigger).
"""

import dataclasses
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
from sensor_msgs.msg import Image
from std_msgs.msg import String
from std_srvs.srv import Trigger
from vision_msgs.msg import Detection2D, Detection2DArray

from . import annotate
from .embedder import make_embedder
from .follow_controller import FollowController, FollowParams
from .params import clamp_follow_params
from .ros_io import (ANNOTATED_QOS, DETECTIONS_QOS, IMAGE_RELIABILITY, OUT_QOS,
                     detections_to_arrays, event_text, image_qos, stamp_ns, state_json,
                     target_msg)
from .target_lock import LockParams, State, TargetLock, unknown_output
from .tracker import IouTracker


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
        self._image_rel = self.declare_parameter('image_reliability', 'reliable').value
        if self._image_rel not in IMAGE_RELIABILITY:
            log.warning(f'unknown image_reliability {self._image_rel!r}; using reliable')
            self._image_rel = 'reliable'
        kind = self.declare_parameter('embedder', 'osnet').value
        device = self.declare_parameter('device', 'cpu').value
        auto_lock = self.declare_parameter('auto_lock', False).value
        self._stale_s = self.declare_parameter('stale_s', 0.8).value
        self._image_buffer = max(1, self.declare_parameter('image_buffer', 15).value)

        self._follow_p, clamp_warnings = clamp_follow_params(self._follow_p)
        for w in clamp_warnings:
            log.warning(f'speed limit {w}')
        self._controller = FollowController(self._follow_p)

        if kind not in ('osnet', 'histogram'):
            log.warning(f'unknown embedder {kind!r}; using histogram')
        self._embedder = make_embedder(kind, device, log=log.warning)
        self._lock = TargetLock(self._lock_p, auto_lock=auto_lock)

        self._mx = threading.Lock()  # guards self._lock and self._lock_waiter
        self._lock_waiter = None  # {'evt': Event, 'result': tuple|None} of the active lock call
        self._img_mx = threading.Lock()  # the buffer is written from its own callback group
        self._images = OrderedDict()  # stamp_ns -> Image
        self._size = (0, 0)
        self._last_det = None
        self._image_topic = image_topic
        self._snap = {'similarity': None, 'n_people': 0, 'unpaired': 0, 'event': None,
                      'reason': None}

        cb = MutuallyExclusiveCallbackGroup()
        img_cb = MutuallyExclusiveCallbackGroup()  # only touches the image buffer
        srv_cb = ReentrantCallbackGroup()
        self._pub_cmd = self.create_publisher(Twist, '/follow/cmd_vel', OUT_QOS)
        self._pub_state = self.create_publisher(String, '/follow/state', OUT_QOS)
        self._pub_target = self.create_publisher(Detection2D, '/follow/target', OUT_QOS)
        self._pub_annot = self.create_publisher(
            Image, '/follow/annotated_image', ANNOTATED_QOS)
        self.create_subscription(Image, image_topic, self._on_image,
                                 image_qos(self._image_rel), callback_group=img_cb)
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
        with self._img_mx:
            self._images[stamp_ns(msg.header.stamp)] = msg
            while len(self._images) > self._image_buffer:
                self._images.popitem(last=False)

    def _on_detections(self, msg: Detection2DArray) -> None:
        now = self._now()
        stamp = stamp_ns(msg.header.stamp)
        det_t = stamp * 1e-9
        if now - det_t > self._stale_s:
            # never act on old boxes: the guest has moved since
            self.get_logger().warning(
                f'dropping detections {now - det_t:.2f}s old (> stale_s {self._stale_s})',
                throttle_duration_sec=10.0)
            self._publish_cmd(0.0, 0.0)
            return
        tracks = self._tracker.update(*detections_to_arrays(msg))

        with self._img_mx:
            img_msg = self._images.get(stamp)
        if img_msg is None:
            self._snap['unpaired'] += 1
            self.get_logger().warning(
                f"no image for this detection stamp ({self._snap['unpaired']} unpaired): "
                f'check {self._image_topic} and image_reliability={self._image_rel}',
                throttle_duration_sec=10.0)
        else:
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
            except Exception as exc:  # never let re-ID kill the node
                self.get_logger().warning(f'embedding failed: {type(exc).__name__}: {exc}',
                                          throttle_duration_sec=10.0)
                return None

        with self._mx:
            # no frame size yet (no image paired so far): the lock cannot score anyone
            out = (self._lock.update(now, tracks, w, h, embed) if w and h
                   else unknown_output(self._lock.state, tracks))
            target = out.target if out.state == State.TRACKING else None
            target_id = self._lock.target_id
            waiter = self._lock_waiter
            if out.event in ('locked', 'lock_failed') and waiter is not None:
                waiter['result'] = (out.event, out.reason, target.id if target else None)
                waiter['evt'].set()
                self._lock_waiter = None
        self._last_det = det_t

        vx, wz = (0.0, 0.0)
        if w and h:
            vx, wz = self._controller.compute(target.box if target else None, w, h)
        self._publish_cmd(vx, wz)
        if target is not None:
            self._pub_target.publish(target_msg(msg.header, target))

        self._snap.update({
            'similarity': None if out.similarity is None else round(out.similarity, 3),
            'n_people': sum(1 for t in tracks if t.visible),
            'event': out.event, 'reason': out.reason})
        if out.check and out.similarity is not None:
            # numbers only (never crops): this is how sim_accept / sim_reject get tuned
            self.get_logger().info(
                f'{out.check} sim={out.similarity:.3f} state={out.state.value}',
                throttle_duration_sec=1.0)
        if out.event:
            self.get_logger().info(event_text(out, target_id))
            self._publish_state(out.event)

        if img_msg is not None and self._pub_annot.get_subscription_count() > 0:
            try:
                ann = annotate.draw(decode(), tracks, out, vx, wz)
                res = fill_image_msg(Image(), ann)
                res.header = img_msg.header
                self._pub_annot.publish(res)
            except Exception as exc:
                self.get_logger().warning(f'annotation failed: {type(exc).__name__}',
                                          throttle_duration_sec=10.0)

    # -- outputs --------------------------------------------------------------
    def _publish_cmd(self, vx: float, wz: float) -> None:
        cmd = Twist()
        cmd.linear.x, cmd.angular.z = float(vx), float(wz)
        self._pub_cmd.publish(cmd)

    def _publish_state(self, event) -> None:
        with self._mx:
            state, target_id = self._lock.state.value, self._lock.target_id
        self._pub_state.publish(String(data=state_json(
            state, target_id, self._snap, self._embedder.name, event)))

    def _on_stale_timer(self) -> None:
        # no fresh detections: never keep a stale velocity alive
        if self._last_det is None or self._now() - self._last_det > self._stale_s:
            self._publish_cmd(0.0, 0.0)

    # -- services -------------------------------------------------------------
    def _srv_lock(self, request, response):
        waiter = {'evt': threading.Event(), 'result': None}
        with self._mx:
            old = self._lock_waiter
            if old is not None:  # a newer request supersedes the older caller
                old['result'] = ('superseded', 'superseded by a newer lock request', None)
                old['evt'].set()
            self._lock_waiter = waiter
            self._lock.request_lock(self._now())
        waiter['evt'].wait(self._lock_p.lock_timeout_s + 1.0)
        with self._mx:
            if self._lock_waiter is waiter:
                self._lock_waiter = None
            result = waiter['result']
        if result is None:
            response.success, response.message = False, 'timeout: no detections'
            return response
        event, reason, target_id = result
        response.success = event == 'locked'
        response.message = f'locked target #{target_id}' if response.success else str(reason)
        return response

    def _srv_release(self, request, response):
        with self._mx:
            self._lock.release()
            waiter = self._lock_waiter
            if waiter is not None:  # do not leave a lock caller waiting for a cleared lock
                waiter['result'] = ('released', 'released', None)
                waiter['evt'].set()
                self._lock_waiter = None
        response.success, response.message = True, 'released'
        return response


def main(args=None) -> int:
    rclpy.init(args=args)
    node = None
    try:
        node = TargetFollower()
        # blocking lock calls each hold a thread; spare ones keep subs/timers running
        executor = MultiThreadedExecutor(num_threads=6)
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
