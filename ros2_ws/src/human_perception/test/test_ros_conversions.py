import pytest

pytest.importorskip('vision_msgs')

from std_msgs.msg import Header  # noqa: E402

from human_perception.detection import PersonDetection  # noqa: E402
from human_perception.ros_conversions import copy_header, to_detection2d_array  # noqa: E402


def _header():
    h = Header(frame_id='cam_optical')
    h.stamp.sec, h.stamp.nanosec = 1700000000, 123456789
    return h


def test_detection_array_fields_and_header_preserved():
    det = PersonDetection(10.0, 20.0, 50.0, 120.0, 0.87, 0)
    arr = to_detection2d_array(_header(), [det])
    assert arr.header.frame_id == 'cam_optical'
    assert (arr.header.stamp.sec, arr.header.stamp.nanosec) == (1700000000, 123456789)
    assert len(arr.detections) == 1
    d = arr.detections[0]
    assert d.header.frame_id == 'cam_optical'
    assert d.header.stamp.nanosec == 123456789
    assert d.bbox.center.position.x == pytest.approx(30.0)
    assert d.bbox.center.position.y == pytest.approx(70.0)
    assert d.bbox.center.theta == 0.0
    assert d.bbox.size_x == pytest.approx(40.0)
    assert d.bbox.size_y == pytest.approx(100.0)
    assert d.results[0].hypothesis.class_id == 'person'
    assert d.results[0].hypothesis.score == pytest.approx(0.87)
    assert d.id == ''  # no identity tracking


def test_empty_detection_array_keeps_header():
    arr = to_detection2d_array(_header(), [])
    assert arr.detections == [] and arr.header.frame_id == 'cam_optical'


def test_custom_class_id_and_header_is_a_copy():
    h = _header()
    arr = to_detection2d_array(h, [PersonDetection(0, 0, 1, 1, 0.5, 0)], class_id='0')
    assert arr.detections[0].results[0].hypothesis.class_id == '0'
    c = copy_header(h)
    h.frame_id = 'changed'
    assert c.frame_id == 'cam_optical'
