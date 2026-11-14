"""
ROS 2 node: detect people in a ``sensor_msgs/Image`` stream with Ultralytics YOLO.

The node is source-agnostic.  It subscribes to one image topic (default
``/camera/image_raw``; remap it for any other camera driver) and publishes:

* ``/perception/humans/detections``      vision_msgs/Detection2DArray
* ``/perception/humans/present``         std_msgs/Bool
* ``/perception/humans/count``           std_msgs/Int32
* ``/perception/humans/annotated_image`` sensor_msgs/Image (optional)
* ``/diagnostics``                       diagnostic_msgs/DiagnosticArray

This is NOT a safety-rated person detector.  Absence of a detection must not
be interpreted as absence of a person.
"""

import sys
import threading
import time
from typing import Any, Callable, Optional

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from sensor_msgs.msg import Image
from std_msgs.msg import Bool, Int32
from vision_msgs.msg import Detection2DArray

from . import diagnostics as diag
from .annotate import draw_detections
from .config import ConfigError, DetectorConfig
from .detection import DetectionPipeline, FrameResult, InferenceError
from .image_codec import fill_image_msg, image_msg_to_bgr, MalformedImageError
from .metrics import fmt_ms, LogThrottle, RateMeter, RollingStats
from .ros_conversions import copy_header, declare_and_get, to_detection2d_array

ModelFactory = Callable[[DetectorConfig, Any], Any]


def image_input_qos(reliability: str) -> QoSProfile:
    """Newest-sample-only input QoS (KEEP_LAST, depth 1): never a backlog."""
    policy = ReliabilityPolicy.RELIABLE if reliability == 'reliable' \
        else ReliabilityPolicy.BEST_EFFORT
    return QoSProfile(reliability=policy, history=HistoryPolicy.KEEP_LAST, depth=1)


# Outputs: reliable (compatible with both reliable and best-effort subscribers).
RESULT_QOS = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                        history=HistoryPolicy.KEEP_LAST, depth=10)
ANNOTATED_QOS = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                           history=HistoryPolicy.KEEP_LAST, depth=1)


def default_model_factory(config: DetectorConfig, logger: Any) -> Any:
    from .model_backend import UltralyticsDetector
    return UltralyticsDetector(config, logger=logger)


class HumanDetectorNode(Node):
    """Latest-frame-only person detector."""

    def __init__(self, model_factory: Optional[ModelFactory] = None, **kwargs: Any) -> None:
        super().__init__('human_detector', **kwargs)
        self.cfg = self._read_config()
        log = self.get_logger()

        self._throttle = LogThrottle(5.0)
        self.received = RateMeter()
        self.processed = RateMeter()
        self.inference_s = RollingStats(300)
        self.frame_age_s = RollingStats(300)
        self.dropped_frames = 0
        self.malformed_frames = 0
        self.inference_errors = 0
        self.last_count = 0
        self.last_input_time: Optional[float] = None
        self.last_error = ''

        self.pub_detections = self.create_publisher(
            Detection2DArray, '/perception/humans/detections', RESULT_QOS)
        self.pub_present = self.create_publisher(Bool, '/perception/humans/present', RESULT_QOS)
        self.pub_count = self.create_publisher(Int32, '/perception/humans/count', RESULT_QOS)
        self.pub_annotated = None
        if self.cfg.publish_annotated_image:
            self.pub_annotated = self.create_publisher(
                Image, '/perception/humans/annotated_image', ANNOTATED_QOS)
        self.pub_diag = None
        if self.cfg.diagnostics_enabled:
            self.pub_diag = self.create_publisher(diag.DiagnosticArray, '/diagnostics', 10)

        # Load the model exactly once.  On failure stay alive and report ERROR
        # on /diagnostics (loudly, throttled) rather than dying silently.
        self.model = None
        self.pipeline: Optional[DetectionPipeline] = None
        self.model_error = ''
        factory = model_factory or default_model_factory
        t0 = time.monotonic()
        try:
            self.model = factory(self.cfg, log)
            person_ids = list(getattr(self.model, 'person_ids', None) or [0])
            if getattr(self.model, 'used_class_fallback', False):
                log.warning('model exposes no class names; using documented COCO fallback '
                            'class index 0 for "person"')
            self.pipeline = DetectionPipeline(self.model, person_ids,
                                              self.cfg.confidence_threshold)
            log.info(f'model ready in {time.monotonic() - t0:.1f}s: '
                     f'{self.cfg.resolved_model_reference()} backend={self.cfg.backend} '
                     f'device={self.cfg.device} imgsz={self.cfg.image_size} '
                     f'person_class_ids={person_ids}')
        except Exception as exc:  # noqa: B902
            self.model_error = f'{type(exc).__name__}: {exc}'
            log.error(f'MODEL UNAVAILABLE - no detections will be published: {self.model_error}')

        self._slot_lock = threading.Condition()
        self._slot: Optional[Image] = None
        self._running = True
        self._worker = threading.Thread(target=self._inference_loop, name='inference',
                                        daemon=True)
        self._worker.start()

        self.sub = self.create_subscription(Image, self.cfg.input_topic, self._on_image,
                                            image_input_qos(self.cfg.input_reliability))
        if self.cfg.diagnostics_enabled:
            self.create_timer(1.0, self._publish_diagnostics)
        self.create_timer(self.cfg.perf_log_period_s, self._log_performance)
        log.info(f'listening on {self.sub.topic_name} ({self.cfg.input_reliability}, depth 1); '
                 f'annotated_image={"on" if self.pub_annotated else "off"}')

    # ------------------------------------------------------------ parameters
    def _read_config(self) -> DetectorConfig:
        defaults = DetectorConfig().__dict__
        return DetectorConfig(**declare_and_get(self, defaults, {'device'})).validate()

    # -------------------------------------------------------------- input
    def _on_image(self, msg: Image) -> None:
        self.received.tick()
        self.last_input_time = time.monotonic()
        with self._slot_lock:
            if self._slot is not None:
                self.dropped_frames += 1  # replaced before inference picked it up
            self._slot = msg
            self._slot_lock.notify()

    def _inference_loop(self) -> None:
        min_period = self.cfg.min_inference_period_s
        next_allowed = 0.0
        while self._running:
            with self._slot_lock:
                while self._running and self._slot is None:
                    self._slot_lock.wait(0.5)
                if not self._running:
                    return
                if min_period is not None:
                    wait = next_allowed - time.monotonic()
                    if wait > 0:
                        # Newer frames may replace the slot meanwhile (counted as drops).
                        self._slot_lock.wait(wait)
                        continue
                msg, self._slot = self._slot, None
            if min_period is not None:
                next_allowed = time.monotonic() + min_period
            if not self.context.ok():
                return  # rclpy is shutting down; publishing would fail
            try:
                self.process_image(msg)
            except Exception as exc:  # noqa: B902 - never let the worker die silently
                if not self.context.ok():
                    return
                self.inference_errors += 1
                self.last_error = f'{type(exc).__name__}: {exc}'
                self._warn('unexpected', f'unexpected processing error: {self.last_error}',
                           error=True)

    # ------------------------------------------------------------ processing
    def process_image(self, msg: Image) -> Optional[FrameResult]:
        """Run detection on one message and publish results. Returns the result."""
        if self.pipeline is None:
            self._warn('no_model', f'frame ignored: model unavailable ({self.model_error})',
                       error=True)
            return None
        try:
            frame = image_msg_to_bgr(msg)
        except (MalformedImageError, ValueError, TypeError) as exc:
            self.malformed_frames += 1
            self._warn('malformed', f'dropping malformed image: {exc}')
            return None
        try:
            result = self.pipeline.process(frame)
        except InferenceError as exc:
            self.inference_errors += 1
            self.last_error = str(exc)
            self._warn('inference', f'inference failed: {exc}', error=True)
            return None

        self.inference_s.add(result.inference_s)
        self.processed.tick()
        self.last_count = result.count
        self._publish(msg, frame, result)
        age = self._frame_age(msg)
        if age is not None:
            self.frame_age_s.add(age)
        return result

    def _publish(self, msg: Image, frame: Any, result: FrameResult) -> None:
        self.pub_detections.publish(
            to_detection2d_array(msg.header, result.detections, self.cfg.output_class_id))
        self.pub_present.publish(Bool(data=result.present))
        self.pub_count.publish(Int32(data=result.count))
        if self.pub_annotated is not None and self.pub_annotated.get_subscription_count() > 0:
            out = Image()
            out.header = copy_header(msg.header)
            fill_image_msg(out, draw_detections(frame, result.detections))
            self.pub_annotated.publish(out)

    def _frame_age(self, msg: Image) -> Optional[float]:
        stamp = msg.header.stamp
        if stamp.sec == 0 and stamp.nanosec == 0:
            return None
        age = (self.get_clock().now() - Time.from_msg(stamp)).nanoseconds / 1e9
        return age if age >= 0 else None

    # --------------------------------------------------------- diagnostics
    def _warn(self, key: str, text: str, error: bool = False) -> None:
        suppressed = self._throttle.should_log(key)
        if suppressed is None:
            return
        if suppressed:
            text += f' ({suppressed} similar messages suppressed)'
        (self.get_logger().error if error else self.get_logger().warning)(text)

    def diagnostic_status(self):
        inf = self.inference_s.summary()
        age = self.frame_age_s.summary()
        input_age = None if self.last_input_time is None else \
            time.monotonic() - self.last_input_time
        if self.pipeline is None:
            level, message = diag.ERROR, f'model unavailable: {self.model_error}'
        elif input_age is None:
            level, message = diag.WARN, f'waiting for images on {self.sub.topic_name}'
        elif input_age > self.cfg.input_timeout_s:
            level, message = diag.WARN, f'no images for {input_age:.1f}s'
        else:
            level, message = diag.OK, f'running, {self.last_count} person(s) in last frame'
        values = {
            'input_topic': self.sub.topic_name,
            'model': self.cfg.resolved_model_reference(),
            'backend': self.cfg.backend,
            'device': self.cfg.device,
            'image_size': self.cfg.image_size,
            'confidence_threshold': self.cfg.confidence_threshold,
            'received_fps': self.received.rate(),
            'processed_fps': self.processed.rate(),
            'inference_mean_ms': None if inf['mean'] is None else inf['mean'] * 1000,
            'inference_p95_ms': None if inf['p95'] is None else inf['p95'] * 1000,
            'frame_age_mean_ms': None if age['mean'] is None else age['mean'] * 1000,
            'frame_age_p95_ms': None if age['p95'] is None else age['p95'] * 1000,
            'frames_processed': self.processed.total,
            'dropped_frames': self.dropped_frames,
            'malformed_frames': self.malformed_frames,
            'inference_errors': self.inference_errors,
            'last_detection_count': self.last_count,
            'last_error': self.last_error or 'none',
        }
        return diag.make_status('human_perception: human_detector', level, message, values)

    def _publish_diagnostics(self) -> None:
        if self.pub_diag is not None:
            self.pub_diag.publish(diag.make_array(self.get_clock().now().to_msg(),
                                                  self.diagnostic_status()))

    def _log_performance(self) -> None:
        if self.pipeline is None:
            self.get_logger().error(f'model unavailable: {self.model_error}')
            return
        inf = self.inference_s.summary()
        age = self.frame_age_s.summary()
        self.get_logger().info(
            f'perf: in={self.received.rate():.1f}fps proc={self.processed.rate():.1f}fps '
            f'infer mean={fmt_ms(inf["mean"])} p95={fmt_ms(inf["p95"])} '
            f'age mean={fmt_ms(age["mean"])} p95={fmt_ms(age["p95"])} '
            f'dropped={self.dropped_frames} malformed={self.malformed_frames} '
            f'errors={self.inference_errors} people={self.last_count}')

    def destroy_node(self) -> None:
        self._running = False
        with self._slot_lock:
            self._slot_lock.notify_all()
        self._worker.join(timeout=5.0)
        super().destroy_node()


def main(args: Optional[list] = None) -> int:
    rclpy.init(args=args)
    node = None
    try:
        node = HumanDetectorNode()
        rclpy.spin(node)
    except ConfigError as exc:
        rclpy.logging.get_logger('human_detector').fatal(f'invalid configuration: {exc}')
        return 2
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
