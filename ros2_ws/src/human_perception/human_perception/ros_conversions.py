"""ROS glue: result -> message conversions and typed parameter declaration."""

from typing import Any, Dict, Iterable, Sequence

from builtin_interfaces.msg import Time
from rcl_interfaces.msg import ParameterDescriptor
from std_msgs.msg import Header
from vision_msgs.msg import Detection2D, Detection2DArray, ObjectHypothesisWithPose

from .config import ConfigError
from .detection import PersonDetection


def copy_header(src: Header) -> Header:
    """Deep-copy a header so outputs carry the *input* stamp and frame_id."""
    return Header(stamp=Time(sec=src.stamp.sec, nanosec=src.stamp.nanosec),
                  frame_id=src.frame_id)


def to_detection2d_array(header: Header, detections: Sequence[PersonDetection],
                         class_id: str = 'person') -> Detection2DArray:
    """
    Build a ``vision_msgs/Detection2DArray`` (Humble / vision_msgs 4.x layout).

    Boxes are axis-aligned in input-image pixels: ``bbox.center.position`` is
    the box centre, ``size_x``/``size_y`` its width/height, ``theta`` is 0.
    ``Detection2D.id`` is left empty on purpose: this milestone does no
    tracking, so there is no identity to report.
    """
    out = Detection2DArray()
    out.header = copy_header(header)
    for det in detections:
        d = Detection2D()
        d.header = copy_header(header)
        cx, cy = det.center
        w, h = det.size
        d.bbox.center.position.x = float(cx)
        d.bbox.center.position.y = float(cy)
        d.bbox.center.theta = 0.0
        d.bbox.size_x = float(w)
        d.bbox.size_y = float(h)
        hyp = ObjectHypothesisWithPose()
        hyp.hypothesis.class_id = class_id
        hyp.hypothesis.score = float(det.score)
        d.results.append(hyp)
        out.detections.append(d)
    return out


def declare_and_get(node: Any, defaults: Dict[str, Any],
                    any_type: Iterable[str] = ()) -> Dict[str, Any]:
    """
    Declare parameters from a defaults dict and return their (coerced) values.

    Float parameters and names in ``any_type`` use dynamic typing, so a YAML
    ``max_inference_fps: 10`` (int) or ``webcam_device: 0`` does not abort the
    node with InvalidParameterTypeException.  Values are coerced back to the
    default's type here; impossible coercions raise ``ConfigError``.
    """
    any_type = set(any_type)
    out: Dict[str, Any] = {}
    for name, default in defaults.items():
        dynamic = name in any_type or isinstance(default, float)
        node.declare_parameter(name, default, ParameterDescriptor(dynamic_typing=dynamic))
        value = node.get_parameter(name).value
        if isinstance(default, str):
            value = '' if value is None else str(value)
        elif isinstance(default, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(f'parameter {name} must be a number, got {value!r}')
            value = float(value)
        out[name] = value
    return out
