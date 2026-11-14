"""End-to-end: video_source (generated frames) -> human_detector (mock model)."""

import numpy as np
import pytest

from ros_helpers import new_executor, rclpy, spin_until  # noqa: I100

from rclpy.parameter import Parameter  # noqa: E402
from std_msgs.msg import Int32  # noqa: E402
from vision_msgs.msg import Detection2DArray  # noqa: E402

from fakes import FakeCapture, FakeModel, ScriptedOpener  # noqa: E402
from human_perception import diagnostics as diag  # noqa: E402
from human_perception.human_detector_node import HumanDetectorNode  # noqa: E402
from human_perception.video_source_node import VideoSourceNode  # noqa: E402


@pytest.fixture(scope='module', autouse=True)
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


def test_generated_frames_flow_through_detector():
    frame = np.random.default_rng(0).integers(0, 255, (120, 160, 3), dtype=np.uint8)
    opener = ScriptedOpener([FakeCapture(frame=frame)])
    src = VideoSourceNode(open_fn=opener, parameter_overrides=[
        Parameter('frame_id', value='pipeline_cam'),
        Parameter('max_publish_fps', value=30.0),
        Parameter('webcam_device', value=0),   # int accepted (dynamic typing)
    ])
    det = HumanDetectorNode(model_factory=lambda c, log: FakeModel([(5, 5, 50, 100, 0.8, 0)]))
    probe = rclpy.create_node('probe')
    dets, counts = [], []
    probe.create_subscription(Detection2DArray, '/perception/humans/detections', dets.append, 10)
    probe.create_subscription(Int32, '/perception/humans/count', counts.append, 10)
    ex = new_executor(src, det, probe)
    try:
        assert spin_until(ex, lambda: len(counts) >= 3, timeout=10.0)
        assert all(c.data == 1 for c in counts)
        assert dets[-1].header.frame_id == 'pipeline_cam'
        assert dets[-1].header.stamp.sec > 0
        assert diag.level_value(src.diagnostic_status()) == diag.OK
        assert diag.level_value(det.diagnostic_status()) == diag.OK
        assert len(det.frame_age_s) > 0
    finally:
        ex.shutdown()
        src.destroy_node()
        det.destroy_node()
        probe.destroy_node()


def test_source_reports_disconnected_diagnostic():
    src = VideoSourceNode(open_fn=ScriptedOpener([]), parameter_overrides=[
        Parameter('source_type', value='rtsp'),
        Parameter('rtsp_url', value='rtsp://admin:pw@192.168.234.1:8554/test'),
        Parameter('reconnect_initial_delay_s', value=0.01),
        Parameter('reconnect_max_delay_s', value=0.05),
    ])
    ex = new_executor(src)
    try:
        assert spin_until(ex, lambda: src.grabber.backoff.attempts >= 2)
        status = src.diagnostic_status()
        assert diag.level_value(status) == diag.ERROR
        assert 'DISCONNECTED' in status.message
        assert all('pw' not in kv.value for kv in status.values)
    finally:
        ex.shutdown()
        src.destroy_node()


def test_slow_publishing_does_not_slow_capture():
    """A slow DDS publish must skip frames, never back-pressure the capture thread."""
    import time as _t
    src = VideoSourceNode(open_fn=ScriptedOpener([FakeCapture()]))
    real_publish = src.pub.publish

    def slow_publish(msg):
        _t.sleep(0.05)
        real_publish(msg)

    src.pub.publish = slow_publish
    try:
        assert spin_until(new_executor(src), lambda: src.published >= 5, timeout=5.0)
        captured = src.grabber.capture_rate.total
        assert captured > 3 * src.published          # capture kept running at full speed
        assert src.skipped_stale > 0                 # stale frames were skipped, not queued
    finally:
        src.destroy_node()


def test_stamp_is_capture_time_not_publish_time():
    import time as _t
    src = VideoSourceNode(open_fn=ScriptedOpener([FakeCapture()]))
    stamps = []
    real_publish = src.pub.publish

    def slow_publish(msg):
        stamps.append((msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9))
        _t.sleep(0.1)
        real_publish(msg)

    src.pub.publish = slow_publish
    try:
        assert spin_until(new_executor(src), lambda: len(stamps) >= 2, timeout=5.0)
        now = src.get_clock().now().nanoseconds * 1e-9
        assert all(now - s < 2.0 for s in stamps)
    finally:
        src.destroy_node()


def test_publisher_thread_stops_quietly_on_context_shutdown():
    """Ctrl+C shuts the context down before destroy_node(); no publish errors may follow."""
    from rclpy.context import Context
    ctx = Context()
    rclpy.init(context=ctx)
    src = VideoSourceNode(open_fn=ScriptedOpener([FakeCapture()]), context=ctx)
    errors = []
    src.get_logger().error = lambda msg, **kw: errors.append(msg)
    try:
        import time as _t
        end = _t.monotonic() + 3.0
        while src.published < 3 and _t.monotonic() < end:
            _t.sleep(0.01)
        assert src.published >= 3
        ctx.shutdown()
        src._publisher_thread.join(timeout=2.0)
        assert not src._publisher_thread.is_alive()
        assert errors == []
    finally:
        src.destroy_node()


def test_source_downscales_before_publishing():
    """D1 streams 1920x1080; a raw bgr8 1080p Image is 6.2 MB. output_width shrinks it."""
    frame = np.zeros((1080, 1920, 3), np.uint8)
    src = VideoSourceNode(open_fn=ScriptedOpener([FakeCapture(frame=frame)]),
                          parameter_overrides=[Parameter('output_width', value=960)])
    sizes = []
    real_publish = src.pub.publish

    def record(msg):
        sizes.append((msg.width, msg.height, msg.step))
        real_publish(msg)

    src.pub.publish = record
    try:
        assert spin_until(new_executor(src), lambda: len(sizes) >= 2, timeout=5.0)
        assert sizes[0] == (960, 540, 960 * 3)
    finally:
        src.destroy_node()
