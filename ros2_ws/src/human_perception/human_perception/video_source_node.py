"""
ROS 2 node: publish frames from a webcam, an RTSP stream, or a video file.

Publishes ``sensor_msgs/Image`` (bgr8) on ``/camera/image_raw`` and its
connection health on ``/diagnostics``.  Frames are never written to disk.
"""

import sys
import threading
import time
from typing import Any, Optional

import rclpy
from rclpy.duration import Duration
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image

from . import diagnostics as diag
from .capture import ConnectionState, FrameGrabber, open_capture, OpenFn
from .config import BackoffConfig, ConfigError, SourceConfig
from .image_codec import downscale_to_width, fill_image_msg
from .metrics import RateMeter
from .ros_conversions import declare_and_get

_STATE_LEVEL = {
    ConnectionState.CONNECTED: diag.OK,
    ConnectionState.STARTING: diag.WARN,
    ConnectionState.CONNECTING: diag.WARN,
    ConnectionState.END_OF_FILE: diag.WARN,
    ConnectionState.RECONNECT_WAIT: diag.ERROR,
    ConnectionState.STOPPED: diag.ERROR,
}


class VideoSourceNode(Node):
    """Capture thread + publisher; only the newest frame is ever published."""

    def __init__(self, open_fn: OpenFn = open_capture, **kwargs: Any) -> None:
        super().__init__('video_source', **kwargs)
        self.cfg, self.output_topic, reliability = self._read_config()
        qos = QoSProfile(reliability=reliability, history=HistoryPolicy.KEEP_LAST, depth=1)
        self.pub = self.create_publisher(Image, self.output_topic, qos)
        self.pub_diag = self.create_publisher(diag.DiagnosticArray, '/diagnostics', 10)
        self.publish_rate = RateMeter()
        self.published = 0
        self.skipped_stale = 0   # captured frames superseded before they could be published
        self._min_period = 1.0 / self.cfg.max_publish_fps if self.cfg.max_publish_fps else 0.0

        # The capture thread only reads and overwrites the latest-frame slot; this
        # publisher thread sends whatever is newest.  A slow publish therefore skips
        # frames instead of back-pressuring the camera/RTSP decoder.
        self.grabber = FrameGrabber(self.cfg, open_fn=open_fn, logger=self.get_logger())
        self._running = True
        self._publisher_thread = threading.Thread(target=self._publish_loop,
                                                  name='frame_publisher', daemon=True)
        self.create_timer(1.0, self._publish_diagnostics)
        self.create_timer(self._perf_period, self._log_performance)
        self.get_logger().info(
            f'source: {self.cfg.describe()} -> {self.pub.topic_name} '
            f'(frame_id={self.cfg.frame_id}, backend={self.cfg.capture_backend}); '
            'frames are not recorded')
        self.grabber.start()
        self._publisher_thread.start()

    def _read_config(self):
        d = SourceConfig()
        b = BackoffConfig()
        p = {
            'source_type': d.source_type, 'webcam_device': d.webcam_device,
            'rtsp_url': d.rtsp_url, 'file_path': d.file_path, 'loop_file': d.loop_file,
            'capture_backend': d.capture_backend, 'rtsp_transport': d.rtsp_transport,
            'rtsp_latency_ms': d.rtsp_latency_ms, 'frame_id': d.frame_id,
            'width': d.width, 'height': d.height, 'fps': d.fps,
            'max_publish_fps': d.max_publish_fps, 'output_width': d.output_width,
            'open_timeout_s': d.open_timeout_s,
            'read_timeout_s': d.read_timeout_s,
            'stale_frame_timeout_s': d.stale_frame_timeout_s,
            'reconnect_initial_delay_s': b.initial_s, 'reconnect_max_delay_s': b.max_s,
            'reconnect_backoff_multiplier': b.multiplier,
            'output_topic': '/camera/image_raw', 'publish_reliability': 'reliable',
            'perf_log_period_s': 10.0,
        }
        v = declare_and_get(self, p, {'webcam_device'})
        backoff = BackoffConfig(v.pop('reconnect_initial_delay_s'),
                                v.pop('reconnect_max_delay_s'),
                                v.pop('reconnect_backoff_multiplier'))
        topic = v.pop('output_topic')
        rel = v.pop('publish_reliability')
        self._perf_period = v.pop('perf_log_period_s')
        if rel not in ('reliable', 'best_effort'):
            raise ConfigError('publish_reliability must be reliable or best_effort')
        if self._perf_period <= 0:
            raise ConfigError('perf_log_period_s must be > 0')
        cfg = SourceConfig(backoff=backoff, **v).validate()
        reliability = ReliabilityPolicy.RELIABLE if rel == 'reliable' \
            else ReliabilityPolicy.BEST_EFFORT
        return cfg, topic, reliability

    def _publish_loop(self) -> None:
        last_seq = 0
        next_allowed = 0.0
        # Stop once rclpy shuts down (Ctrl+C invalidates the context before destroy_node).
        while self._running and self.context.ok():
            latest = self.grabber.wait_for_frame(last_seq, timeout=0.5)
            if latest is None:
                continue
            seq, frame, capture_mono = latest
            if self._min_period:
                wait = next_allowed - time.monotonic()
                if wait > 0:
                    time.sleep(wait)  # newer frames may arrive meanwhile; take newest
                    seq, frame, capture_mono = self.grabber.get_latest()
                next_allowed = time.monotonic() + self._min_period
            if last_seq:
                self.skipped_stale += max(0, seq - last_seq - 1)
            last_seq = seq
            try:
                self._publish(frame, capture_mono)
            except Exception as exc:  # noqa: B902 - keep publishing; report throttled
                if not self.context.ok():
                    return  # shutting down: not an error
                self.get_logger().error(f'publish failed: {type(exc).__name__}: {exc}',
                                        throttle_duration_sec=5.0)

    def _publish(self, frame, capture_mono: float) -> None:
        # Stamp with the CAPTURE time (ROS clock), so downstream frame age includes
        # any time the frame waited before publication.
        waited_ns = int(max(0.0, time.monotonic() - capture_mono) * 1e9)
        stamp = self.get_clock().now() - Duration(nanoseconds=waited_ns)
        msg = Image()
        msg.header.stamp = stamp.to_msg()
        msg.header.frame_id = self.cfg.frame_id
        fill_image_msg(msg, downscale_to_width(frame, self.cfg.output_width))
        self.pub.publish(msg)
        self.published += 1
        self.publish_rate.tick()

    def diagnostic_status(self):
        g = self.grabber
        level = _STATE_LEVEL.get(g.state, diag.WARN)
        message = g.state.value
        if g.is_stale():
            limit = g.config.stale_frame_timeout_s
            level, message = diag.WARN, f'connected but no frame for >{limit}s'
        if g.state == ConnectionState.RECONNECT_WAIT:
            message = (f'DISCONNECTED: {g.last_error}; retry #{g.backoff.attempts} '
                       f'in {g.next_retry_in_s:.1f}s')
        age = g.frame_age()
        values = {
            'source': self.cfg.describe(),
            'state': g.state.value,
            'capture_fps': g.capture_rate.rate(),
            'publish_fps': self.publish_rate.rate(),
            'resolution': f'{g.resolution[0]}x{g.resolution[1]}',
            'output_width': self.cfg.output_width or 'native',
            'last_frame_age_ms': None if age is None else age * 1000.0,
            'frames_published': self.published,
            'skipped_stale_frames': self.skipped_stale,
            'invalid_frames': g.invalid_frame_count,
            'reconnects': g.reconnect_count,
            'consecutive_failures': g.backoff.attempts,
            'last_error': g.last_error or 'none',
        }
        return diag.make_status('human_perception: video_source', level, message, values)

    def _publish_diagnostics(self) -> None:
        self.pub_diag.publish(diag.make_array(self.get_clock().now().to_msg(),
                                              self.diagnostic_status()))

    def _log_performance(self) -> None:
        g = self.grabber
        line = (f'perf: state={g.state.value} capture={g.capture_rate.rate():.1f}fps '
                f'publish={self.publish_rate.rate():.1f}fps skipped_stale={self.skipped_stale} '
                f'res={g.resolution[0]}x{g.resolution[1]} reconnects={g.reconnect_count}')
        if g.state == ConnectionState.CONNECTED:
            self.get_logger().info(line)
        else:
            self.get_logger().warning(line)

    def destroy_node(self) -> None:
        self._running = False
        self.grabber.stop()
        self._publisher_thread.join(timeout=2.0)
        super().destroy_node()


def main(args: Optional[list] = None) -> int:
    rclpy.init(args=args)
    node = None
    try:
        node = VideoSourceNode()
        rclpy.spin(node)
    except ConfigError as exc:
        rclpy.logging.get_logger('video_source').fatal(f'invalid configuration: {exc}')
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
