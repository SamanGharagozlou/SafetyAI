"""
Validated configuration objects for the source and detector nodes.

These dataclasses are deliberately ROS-free so they can be unit tested and
reused by the benchmark tool.  ROS nodes build them from declared parameters
and call ``validate()`` once at start-up; invalid values raise ``ConfigError``
so a misconfigured node fails loudly at launch instead of publishing
misleading data.
"""

from dataclasses import dataclass
import os
import re
from typing import Optional, Tuple
from urllib.parse import urlsplit, urlunsplit


class ConfigError(ValueError):
    """Raised when a node configuration is invalid."""


SOURCE_TYPES = ('webcam', 'rtsp', 'file')
CAPTURE_BACKENDS = ('auto', 'ffmpeg', 'gstreamer', 'v4l2')
RTSP_TRANSPORTS = ('tcp', 'udp')

BACKENDS = ('pytorch', 'onnx', 'openvino', 'tensorrt')
RELIABILITIES = ('best_effort', 'reliable')
_DEVICE_RE = re.compile(r'^(cpu|mps|cuda(:\d+)?|\d+(,\d+)*|intel:(cpu|gpu|npu))$')


def url_has_credentials(url: str) -> bool:
    """Return True when ``url`` embeds user-info (``user:pass@host``)."""
    try:
        return '@' in urlsplit(url).netloc
    except ValueError:
        return False


def redact_url(url: str) -> str:
    """
    Return ``url`` with any user-info (credentials) removed.

    ``rtsp://user:secret@10.0.0.2:8554/x`` -> ``rtsp://***@10.0.0.2:8554/x``.
    Query strings are dropped as well because some cameras pass tokens there.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return '<unparseable-url>'
    if not parts.scheme or not parts.netloc:
        return url
    netloc = parts.netloc
    if '@' in netloc:
        netloc = '***@' + netloc.rsplit('@', 1)[1]
    query = '<redacted>' if parts.query else ''
    return urlunsplit((parts.scheme, netloc, parts.path, query, ''))


@dataclass
class BackoffConfig:
    """Bounded exponential reconnect backoff."""

    initial_s: float = 0.5
    max_s: float = 10.0
    multiplier: float = 2.0

    def validate(self) -> 'BackoffConfig':
        if self.initial_s <= 0.0:
            raise ConfigError('reconnect_initial_delay_s must be > 0')
        if self.max_s < self.initial_s:
            raise ConfigError('reconnect_max_delay_s must be >= reconnect_initial_delay_s')
        if self.multiplier < 1.0:
            raise ConfigError('reconnect_backoff_multiplier must be >= 1.0')
        return self


@dataclass
class SourceConfig:
    """Configuration of the video-source node."""

    source_type: str = 'webcam'
    webcam_device: str = '0'
    rtsp_url: str = ''
    file_path: str = ''
    loop_file: bool = True
    capture_backend: str = 'auto'
    rtsp_transport: str = 'tcp'
    rtsp_latency_ms: int = 100
    frame_id: str = 'camera_optical_frame'
    width: int = 0
    height: int = 0
    fps: float = 0.0
    max_publish_fps: float = 0.0
    # Downscale wider frames to this width before publishing (0 = native). The detector
    # letterboxes to image_size (640) anyway, so e.g. 960 loses nothing for detection
    # while cutting a 1080p bgr8 message from 6.2 MB to 1.6 MB.
    output_width: int = 0
    open_timeout_s: float = 5.0
    read_timeout_s: float = 5.0
    stale_frame_timeout_s: float = 2.0
    backoff: BackoffConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.backoff is None:
            self.backoff = BackoffConfig()

    def validate(self) -> 'SourceConfig':
        if self.source_type not in SOURCE_TYPES:
            raise ConfigError(
                f'source_type must be one of {SOURCE_TYPES}, got {self.source_type!r}')
        if self.capture_backend not in CAPTURE_BACKENDS:
            raise ConfigError(
                f'capture_backend must be one of {CAPTURE_BACKENDS}, '
                f'got {self.capture_backend!r}')
        if self.rtsp_transport not in RTSP_TRANSPORTS:
            raise ConfigError(f'rtsp_transport must be one of {RTSP_TRANSPORTS}')
        if self.source_type == 'rtsp':
            if not self.rtsp_url:
                raise ConfigError(
                    'source_type=rtsp requires rtsp_url (set the ROS parameter or '
                    'pass rtsp_url:=rtsp://host:port/path to the launch file)')
            scheme = urlsplit(self.rtsp_url).scheme.lower()
            if scheme not in ('rtsp', 'rtsps'):
                raise ConfigError(
                    f'rtsp_url must start with rtsp:// or rtsps://, got '
                    f'{redact_url(self.rtsp_url)!r}')
        if self.source_type == 'webcam' and not str(self.webcam_device).strip():
            raise ConfigError('webcam_device must be an index (e.g. 0) or a device path')
        if self.source_type == 'file' and not self.file_path:
            raise ConfigError('source_type=file requires file_path')
        if not self.frame_id:
            raise ConfigError('frame_id must not be empty')
        if self.output_width and self.output_width < 32:
            raise ConfigError('output_width must be 0 (native) or >= 32')
        for name in ('width', 'height', 'rtsp_latency_ms', 'output_width'):
            if getattr(self, name) < 0:
                raise ConfigError(f'{name} must be >= 0')
        for name in ('fps', 'max_publish_fps'):
            if getattr(self, name) < 0.0:
                raise ConfigError(f'{name} must be >= 0 (0 disables the limit)')
        for name in ('open_timeout_s', 'read_timeout_s', 'stale_frame_timeout_s'):
            if getattr(self, name) <= 0.0:
                raise ConfigError(f'{name} must be > 0')
        self.backoff.validate()
        return self

    def webcam_target(self):
        """Return an int index for numeric devices, else the device path."""
        dev = str(self.webcam_device).strip()
        return int(dev) if dev.isdigit() else dev

    def describe(self) -> str:
        """Human readable, credential-free description of the source."""
        if self.source_type == 'rtsp':
            return f'rtsp {redact_url(self.rtsp_url)}'
        if self.source_type == 'file':
            return f'file {os.path.basename(self.file_path)}'
        return f'webcam {self.webcam_device}'


@dataclass
class DetectorConfig:
    """Configuration of the human-detector node."""

    model_name: str = 'yolo26n.pt'
    model_path: str = ''
    weights_dir: str = '~/.cache/human_perception/models'
    backend: str = 'pytorch'
    device: str = 'cpu'
    confidence_threshold: float = 0.45
    iou_threshold: float = 0.7
    image_size: int = 640
    half: bool = False
    max_detections: int = 100
    max_inference_fps: float = 0.0
    input_topic: str = '/camera/image_raw'
    # Sensor-data QoS (best_effort, KEEP_LAST depth 1) by default.  'reliable' keeps
    # depth 1 (still newest-frame-only) but survives large raw images on hosts with
    # small kernel UDP buffers; see README "QoS and large images".
    input_reliability: str = 'best_effort'
    publish_annotated_image: bool = False
    diagnostics_enabled: bool = True
    output_class_id: str = 'person'
    input_timeout_s: float = 2.0
    perf_log_period_s: float = 10.0
    warmup_runs: int = 1

    def validate(self) -> 'DetectorConfig':
        if not self.model_path and not self.model_name:
            raise ConfigError('either model_path or model_name must be set')
        if self.backend not in BACKENDS:
            raise ConfigError(f'backend must be one of {BACKENDS}, got {self.backend!r}')
        if not _DEVICE_RE.match(self.device):
            raise ConfigError(
                f'device {self.device!r} is not recognised; use cpu, cuda:0, 0, mps, '
                'or intel:cpu/intel:gpu for OpenVINO')
        is_cuda = self.device.startswith('cuda') or self.device[0].isdigit()
        if self.backend == 'tensorrt' and not is_cuda:
            raise ConfigError('backend=tensorrt requires a CUDA device (e.g. device: cuda:0)')
        if self.half and self.device == 'cpu':
            raise ConfigError('half=true (FP16) is not supported on device=cpu')
        if not 0.0 < self.confidence_threshold <= 1.0:
            raise ConfigError('confidence_threshold must be in (0, 1]')
        if not 0.0 < self.iou_threshold <= 1.0:
            raise ConfigError('iou_threshold must be in (0, 1]')
        if self.image_size < 32 or self.image_size % 32 != 0:
            raise ConfigError('image_size must be a positive multiple of 32 (e.g. 320, 640)')
        if self.max_detections < 1:
            raise ConfigError('max_detections must be >= 1')
        if self.max_inference_fps < 0.0:
            raise ConfigError('max_inference_fps must be >= 0 (0 = unlimited)')
        if not self.input_topic:
            raise ConfigError('input_topic must not be empty')
        if self.input_reliability not in RELIABILITIES:
            raise ConfigError(f'input_reliability must be one of {RELIABILITIES}')
        if not self.output_class_id:
            raise ConfigError('output_class_id must not be empty')
        if self.input_timeout_s <= 0.0 or self.perf_log_period_s <= 0.0:
            raise ConfigError('input_timeout_s and perf_log_period_s must be > 0')
        if self.warmup_runs < 0:
            raise ConfigError('warmup_runs must be >= 0')
        self._check_model_matches_backend()
        return self

    def _check_model_matches_backend(self) -> None:
        ref = self.resolved_model_reference().rstrip('/')
        expected: Tuple[str, ...] = {
            'pytorch': ('.pt',),
            'onnx': ('.onnx',),
            'openvino': ('_openvino_model',),
            'tensorrt': ('.engine',),
        }[self.backend]
        if not ref.endswith(expected):
            raise ConfigError(
                f'backend={self.backend} expects a model ending in {expected[0]!r}, got '
                f'{os.path.basename(ref)!r}. Export it first (see README "Optional '
                'deployment backends"); engines are never exported automatically.')

    def resolved_model_reference(self) -> str:
        """
        Return the path or name passed to Ultralytics.

        ``model_path`` wins when set.  A bare ``model_name`` (no directory part)
        is placed in ``weights_dir`` so Ultralytics downloads it there instead
        of into whatever the current working directory happens to be.
        """
        if self.model_path:
            return os.path.expanduser(self.model_path)
        if os.sep in self.model_name or not self.weights_dir:
            return os.path.expanduser(self.model_name)
        return os.path.join(os.path.expanduser(self.weights_dir), self.model_name)

    @property
    def min_inference_period_s(self) -> Optional[float]:
        if self.max_inference_fps <= 0.0:
            return None
        return 1.0 / self.max_inference_fps
