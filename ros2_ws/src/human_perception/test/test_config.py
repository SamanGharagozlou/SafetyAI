import os

import pytest

from human_perception.config import (BackoffConfig, ConfigError, DetectorConfig, redact_url,
                                     SourceConfig)


def test_detector_defaults_are_valid_and_match_spec():
    cfg = DetectorConfig().validate()
    assert cfg.model_name == 'yolo26n.pt'
    assert cfg.confidence_threshold == 0.45
    assert cfg.image_size == 640
    assert cfg.device == 'cpu'
    assert cfg.input_topic == '/camera/image_raw'
    assert cfg.publish_annotated_image is False
    assert cfg.min_inference_period_s is None


@pytest.mark.parametrize('field,value', [
    ('confidence_threshold', 0.0), ('confidence_threshold', 1.5),
    ('iou_threshold', 0.0), ('image_size', 0), ('image_size', 650),
    ('max_inference_fps', -1.0), ('backend', 'caffe'), ('device', 'gpu'),
    ('input_topic', ''), ('max_detections', 0), ('warmup_runs', -1),
])
def test_detector_rejects_invalid_values(field, value):
    with pytest.raises(ConfigError):
        DetectorConfig(**{field: value}).validate()


def test_detector_requires_model():
    with pytest.raises(ConfigError):
        DetectorConfig(model_name='', model_path='').validate()


def test_yolo26s_is_a_valid_alternative():
    assert DetectorConfig(model_name='yolo26s.pt').validate()


def test_backend_must_match_model_format():
    with pytest.raises(ConfigError, match='onnx'):
        DetectorConfig(backend='onnx').validate()  # yolo26n.pt is not ONNX
    DetectorConfig(backend='onnx', model_path='/m/yolo26n.onnx').validate()
    DetectorConfig(backend='openvino', model_path='/m/yolo26n_openvino_model/',
                   device='intel:cpu').validate()


def test_tensorrt_requires_cuda_device_and_engine():
    with pytest.raises(ConfigError, match='CUDA'):
        DetectorConfig(backend='tensorrt', model_path='/m/x.engine', device='cpu').validate()
    DetectorConfig(backend='tensorrt', model_path='/m/x.engine', device='cuda:0').validate()
    DetectorConfig(backend='tensorrt', model_path='/m/x.engine', device='0').validate()


def test_half_precision_rejected_on_cpu():
    with pytest.raises(ConfigError):
        DetectorConfig(half=True).validate()


def test_model_reference_resolution(tmp_path):
    cfg = DetectorConfig(weights_dir=str(tmp_path))
    assert cfg.resolved_model_reference() == os.path.join(str(tmp_path), 'yolo26n.pt')
    cfg = DetectorConfig(model_path='/opt/models/custom.pt', model_name='yolo26s.pt')
    assert cfg.resolved_model_reference() == '/opt/models/custom.pt'


def test_max_inference_fps_period():
    assert DetectorConfig(max_inference_fps=4.0).validate().min_inference_period_s == 0.25


def test_source_defaults_webcam_zero():
    cfg = SourceConfig().validate()
    assert cfg.source_type == 'webcam'
    assert cfg.webcam_target() == 0
    assert SourceConfig(webcam_device='/dev/video2').webcam_target() == '/dev/video2'


def test_rtsp_requires_url():
    with pytest.raises(ConfigError, match='rtsp_url'):
        SourceConfig(source_type='rtsp').validate()
    with pytest.raises(ConfigError):
        SourceConfig(source_type='rtsp', rtsp_url='http://x/y').validate()


@pytest.mark.parametrize('url', ['rtsp://192.168.234.1:8554/test',
                                 'rtsp://192.168.168.168:8554/test',
                                 'rtsp://10.1.2.3:554/any/other/path'])
def test_any_rtsp_url_is_accepted(url):
    assert SourceConfig(source_type='rtsp', rtsp_url=url).validate()


@pytest.mark.parametrize('field,value', [
    ('source_type', 'usb'), ('capture_backend', 'dshow'), ('frame_id', ''),
    ('width', -1), ('fps', -1.0), ('open_timeout_s', 0.0), ('rtsp_transport', 'http'),
])
def test_source_rejects_invalid_values(field, value):
    with pytest.raises(ConfigError):
        SourceConfig(**{field: value}).validate()


def test_file_source_requires_path():
    with pytest.raises(ConfigError):
        SourceConfig(source_type='file').validate()


@pytest.mark.parametrize('b', [BackoffConfig(0.0, 1.0), BackoffConfig(2.0, 1.0),
                               BackoffConfig(1.0, 2.0, 0.5)])
def test_backoff_validation(b):
    with pytest.raises(ConfigError):
        b.validate()


def test_redact_url_hides_credentials_and_query():
    red = redact_url('rtsp://admin:hunter2@192.168.1.5:554/stream?token=abc')
    assert 'admin' not in red and 'hunter2' not in red and 'abc' not in red
    assert red.startswith('rtsp://***@192.168.1.5:554/stream')
    assert redact_url('rtsp://192.168.234.1:8554/test') == 'rtsp://192.168.234.1:8554/test'
    assert redact_url('') == ''


def test_describe_never_contains_credentials():
    cfg = SourceConfig(source_type='rtsp', rtsp_url='rtsp://u:p4ss@h:1/x').validate()
    assert 'p4ss' not in cfg.describe()


def test_input_reliability_validation():
    assert DetectorConfig().input_reliability == 'best_effort'  # sensor-data QoS default
    DetectorConfig(input_reliability='reliable').validate()
    with pytest.raises(ConfigError):
        DetectorConfig(input_reliability='sometimes').validate()


def test_url_has_credentials():
    from human_perception.config import url_has_credentials
    assert url_has_credentials('rtsp://u:p@h:1/x')
    assert not url_has_credentials('rtsp://192.168.234.1:8554/test')


def test_output_width_validation():
    assert SourceConfig().output_width == 0  # default: publish native resolution
    SourceConfig(output_width=960).validate()
    with pytest.raises(ConfigError):
        SourceConfig(output_width=-1).validate()
    with pytest.raises(ConfigError):
        SourceConfig(output_width=8).validate()  # nonsensically small
