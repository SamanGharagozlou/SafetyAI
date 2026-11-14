import pytest

from ros_helpers import make_image, new_executor, rclpy, spin_until  # noqa: I100

from rclpy.parameter import Parameter  # noqa: E402
from rclpy.qos import QoSProfile  # noqa: E402
from sensor_msgs.msg import Image  # noqa: E402
from std_msgs.msg import Bool, Int32  # noqa: E402
from vision_msgs.msg import Detection2DArray  # noqa: E402

from fakes import FakeModel  # noqa: E402
from human_perception import diagnostics as diag  # noqa: E402
from human_perception.config import ConfigError  # noqa: E402
from human_perception.human_detector_node import HumanDetectorNode  # noqa: E402

TWO_PEOPLE = [(1, 1, 20, 40, 0.9, 0), (30, 2, 60, 45, 0.6, 0), (5, 5, 9, 9, 0.99, 2)]


@pytest.fixture(scope='module', autouse=True)
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


class Collector:
    def __init__(self, node):
        q = QoSProfile(depth=10)
        self.dets, self.present, self.count, self.annotated = [], [], [], []
        node.create_subscription(Detection2DArray, '/perception/humans/detections',
                                 self.dets.append, q)
        node.create_subscription(Bool, '/perception/humans/present', self.present.append, q)
        node.create_subscription(Int32, '/perception/humans/count', self.count.append, q)
        node.create_subscription(Image, '/perception/humans/annotated_image',
                                 self.annotated.append, q)


def _detector(rows=TWO_PEOPLE, factory=None, **params):
    overrides = [Parameter(k, value=v) for k, v in params.items()]
    return HumanDetectorNode(model_factory=factory or (lambda c, log: FakeModel(rows)),
                             parameter_overrides=overrides)


def test_outputs_present_count_and_header_preserved():
    det = _detector(publish_annotated_image=True)
    probe = rclpy.create_node('probe')
    col = Collector(probe)
    ex = new_executor(det, probe)
    try:
        assert spin_until(ex, lambda: det.pub_annotated.get_subscription_count() > 0)
        res = det.process_image(make_image(sec=42, nanosec=7, frame_id='cam_a'))
        assert res.count == 2
        assert spin_until(ex, lambda: col.dets and col.present and col.count and col.annotated)
        assert col.present[0].data is True
        assert col.count[0].data == 2
        arr = col.dets[0]
        assert (arr.header.stamp.sec, arr.header.stamp.nanosec, arr.header.frame_id) == \
            (42, 7, 'cam_a')
        assert all(d.results[0].hypothesis.class_id == 'person' for d in arr.detections)
        ann = col.annotated[0]
        assert (ann.header.stamp.sec, ann.header.frame_id, ann.encoding) == (42, 'cam_a', 'bgr8')
    finally:
        ex.shutdown()
        det.destroy_node()
        probe.destroy_node()


def test_no_people_publishes_false_and_zero():
    det = _detector(rows=[(5, 5, 9, 9, 0.99, 2)])
    probe = rclpy.create_node('probe')
    col = Collector(probe)
    ex = new_executor(det, probe)
    try:
        det.process_image(make_image())
        assert spin_until(ex, lambda: col.dets and col.present and col.count)
        assert col.present[0].data is False and col.count[0].data == 0
        assert col.dets[0].detections == []
        assert col.annotated == []  # disabled by default
    finally:
        ex.shutdown()
        det.destroy_node()
        probe.destroy_node()


def test_malformed_frame_is_counted_not_fatal():
    det = _detector()
    try:
        bad = make_image()
        bad.data = bad.data[:10]
        assert det.process_image(bad) is None
        bad2 = make_image()
        bad2.encoding = '32FC1'
        assert det.process_image(bad2) is None
        assert det.malformed_frames == 2
        assert det.process_image(make_image()).count == 2  # still healthy
    finally:
        det.destroy_node()


def test_inference_exception_is_counted_and_reported():
    det = _detector(factory=lambda c, log: FakeModel(raise_exc=RuntimeError('boom')))
    try:
        assert det.process_image(make_image()) is None
        assert det.inference_errors == 1
        assert 'boom' in det.last_error
    finally:
        det.destroy_node()


def test_unavailable_model_keeps_node_alive_with_error_diagnostic():
    def failing(cfg, log):
        raise FileNotFoundError('yolo26n.pt missing and offline')

    det = _detector(factory=failing)
    probe = rclpy.create_node('probe')
    col = Collector(probe)
    ex = new_executor(det, probe)
    try:
        assert det.pipeline is None
        assert det.process_image(make_image()) is None
        status = det.diagnostic_status()
        assert diag.level_value(status) == diag.ERROR
        assert 'offline' in status.message
        spin_until(ex, lambda: False, timeout=0.3)
        assert col.present == [] and col.count == []  # unknown != "no person"
    finally:
        ex.shutdown()
        det.destroy_node()
        probe.destroy_node()


def test_invalid_parameters_fail_loudly():
    with pytest.raises(ConfigError):
        _detector(confidence_threshold=1.5)
    with pytest.raises(ConfigError):
        _detector(image_size=100)


def test_parameters_overridable_and_device_int_accepted():
    det = _detector(confidence_threshold=0.6, image_size=320, max_inference_fps=5,
                    model_name='yolo26s.pt')
    try:
        assert det.cfg.confidence_threshold == 0.6
        assert det.cfg.image_size == 320
        assert det.cfg.max_inference_fps == 5.0
        assert det.cfg.model_name == 'yolo26s.pt'
    finally:
        det.destroy_node()


def test_subscription_path_drops_stale_frames():
    """Frames arriving faster than inference are dropped, not queued."""
    import time as _t

    class SlowModel(FakeModel):
        def predict(self, frame):
            _t.sleep(0.05)
            return super().predict(frame)

    det = _detector(factory=lambda c, log: SlowModel(TWO_PEOPLE))
    probe = rclpy.create_node('probe')
    pub = probe.create_publisher(Image, '/camera/image_raw', 10)
    ex = new_executor(det, probe)
    try:
        assert spin_until(ex, lambda: pub.get_subscription_count() > 0)
        for i in range(40):
            pub.publish(make_image(sec=i))
            ex.spin_once(timeout_sec=0.005)
        assert spin_until(ex, lambda: det.processed.total > 0)
        spin_until(ex, lambda: False, timeout=0.3)
        assert det.received.total > det.processed.total
        assert det.processed.total < 40
    finally:
        ex.shutdown()
        det.destroy_node()
        probe.destroy_node()


def test_non_numeric_float_parameter_rejected():
    with pytest.raises(ConfigError, match='must be a number'):
        _detector(confidence_threshold='high')


@pytest.mark.parametrize('rel,policy', [('best_effort', 'BEST_EFFORT'), ('reliable', 'RELIABLE')])
def test_input_qos_is_depth_one_with_selected_reliability(rel, policy):
    from rclpy.qos import HistoryPolicy, ReliabilityPolicy
    det = _detector(input_reliability=rel)
    try:
        qos = det.sub.qos_profile
        assert qos.depth == 1 and qos.history == HistoryPolicy.KEEP_LAST
        assert qos.reliability == getattr(ReliabilityPolicy, policy)
    finally:
        det.destroy_node()
