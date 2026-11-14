"""
Camera / RTSP capture with automatic reconnection and a latest-frame slot.

A dedicated thread reads frames as fast as the source delivers them.  This is
what keeps latency low for RTSP: if nobody drains the decoder, FFmpeg/GStreamer
buffer frames internally and the consumer sees seconds-old images.  Only the
newest frame is retained (queue depth one); consumers never see a backlog.
"""

import enum
import os
import threading
import time
from typing import Any, Callable, Optional, Tuple

import numpy as np

from .backoff import ExponentialBackoff
from .config import SourceConfig, url_has_credentials
from .metrics import LogThrottle, RateMeter


class ConnectionState(enum.Enum):
    STARTING = 'starting'
    CONNECTING = 'connecting'
    CONNECTED = 'connected'
    RECONNECT_WAIT = 'disconnected (waiting to reconnect)'
    END_OF_FILE = 'end of file'
    STOPPED = 'stopped'


FrameCallback = Callable[[np.ndarray, float], None]
OpenFn = Callable[[SourceConfig], Any]


def opencv_has_gstreamer() -> bool:
    import cv2
    for line in cv2.getBuildInformation().splitlines():
        if line.strip().startswith('GStreamer:'):
            return 'YES' in line
    return False


def build_gstreamer_rtsp_pipeline(url: str, latency_ms: int, transport: str) -> str:
    """Build a GStreamer RTSP pipeline that always hands out the newest frame."""
    protocols = 'tcp' if transport == 'tcp' else 'udp'
    return (f'rtspsrc location="{url}" latency={latency_ms} protocols={protocols} '
            'drop-on-latency=true ! decodebin ! videoconvert ! video/x-raw,format=BGR ! '
            'appsink drop=true max-buffers=1 sync=false')


def open_capture(config: SourceConfig) -> Any:
    """Open a ``cv2.VideoCapture`` for the configured source (may be not-opened)."""
    import cv2

    params = []
    if hasattr(cv2, 'CAP_PROP_OPEN_TIMEOUT_MSEC'):
        params += [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(config.open_timeout_s * 1000)]
    if hasattr(cv2, 'CAP_PROP_READ_TIMEOUT_MSEC'):
        params += [cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(config.read_timeout_s * 1000)]

    if config.source_type == 'rtsp':
        backend = config.capture_backend
        if backend == 'gstreamer':
            if not opencv_has_gstreamer():
                raise RuntimeError(
                    'capture_backend=gstreamer but this OpenCV build has no GStreamer support '
                    '(pip opencv-python wheels do not). Use capture_backend:=ffmpeg or the '
                    'system python3-opencv.')
            pipeline = build_gstreamer_rtsp_pipeline(
                config.rtsp_url, config.rtsp_latency_ms, config.rtsp_transport)
            return cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if url_has_credentials(config.rtsp_url):
            # FFmpeg/OpenCV native logging bypasses our redaction and can echo the
            # full URL; silence it when credentials are embedded (user may override).
            os.environ.setdefault('OPENCV_FFMPEG_LOGLEVEL', '-8')
            os.environ.setdefault('OPENCV_LOG_LEVEL', 'ERROR')
            cv2.setLogLevel(2)  # 2 = ERROR; suppresses the native WARN lines
        # FFmpeg: low-latency flags, transport selection.  Respect a user override.
        os.environ.setdefault(
            'OPENCV_FFMPEG_CAPTURE_OPTIONS',
            f'rtsp_transport;{config.rtsp_transport}|fflags;nobuffer|flags;low_delay')
        cap = cv2.VideoCapture(config.rtsp_url, cv2.CAP_FFMPEG, params)
    elif config.source_type == 'file':
        cap = cv2.VideoCapture(config.file_path)
    else:
        api = cv2.CAP_V4L2 if config.capture_backend in ('auto', 'v4l2') and os.name == 'posix' \
            else cv2.CAP_ANY
        cap = cv2.VideoCapture(config.webcam_target(), api)
        if config.width > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.width)
        if config.height > 0:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.height)
        if config.fps > 0:
            cap.set(cv2.CAP_PROP_FPS, config.fps)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:  # noqa: B902 - not every backend supports it
        pass
    return cap


def normalize_frame(frame: Any) -> Optional[np.ndarray]:
    """Return a contiguous HxWx3 uint8 BGR frame or ``None`` if unusable."""
    if not isinstance(frame, np.ndarray) or frame.size == 0 or frame.dtype != np.uint8:
        return None
    if frame.ndim == 2:
        frame = np.repeat(frame[:, :, None], 3, axis=2)
    elif frame.ndim == 3 and frame.shape[2] == 4:
        frame = frame[:, :, :3]
    elif frame.ndim != 3 or frame.shape[2] != 3:
        return None
    return np.ascontiguousarray(frame)


class FrameGrabber:
    """Background capture loop with bounded exponential reconnect backoff."""

    def __init__(self, config: SourceConfig, on_frame: Optional[FrameCallback] = None,
                 open_fn: OpenFn = open_capture, logger: Optional[Any] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self._on_frame = on_frame
        self._open_fn = open_fn
        self._log = logger
        self._clock = clock
        b = config.backoff
        self.backoff = ExponentialBackoff(b.initial_s, b.max_s, b.multiplier, jitter=0.1)
        self.capture_rate = RateMeter()
        self._throttle = LogThrottle(10.0)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Condition()
        self._latest: Optional[Tuple[int, np.ndarray, float]] = None
        self._seq = 0
        self.state = ConnectionState.STARTING
        self.last_error = ''
        self.reconnect_count = 0
        self.invalid_frame_count = 0
        self.last_frame_time: Optional[float] = None
        self.next_retry_in_s = 0.0
        self.resolution: Tuple[int, int] = (0, 0)

    # ------------------------------------------------------------------ API
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name='frame_grabber', daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        with self._lock:
            self._lock.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)
        self.state = ConnectionState.STOPPED

    def get_latest(self) -> Optional[Tuple[int, np.ndarray, float]]:
        """Return ``(sequence, frame, monotonic_capture_time)`` of the newest frame."""
        with self._lock:
            return self._latest

    def wait_for_frame(self, after_seq: int, timeout: float
                       ) -> Optional[Tuple[int, np.ndarray, float]]:
        """
        Block until a frame newer than ``after_seq`` exists; return the newest.

        Returns ``None`` on timeout or stop.  Consumers that fall behind simply
        get the newest frame; intermediate frames are skipped, never queued.
        """
        with self._lock:
            ok = self._lock.wait_for(
                lambda: self._stop.is_set() or (
                    self._latest is not None and self._latest[0] > after_seq),
                timeout)
            if not ok or self._latest is None or self._latest[0] <= after_seq:
                return None
            return self._latest

    def frame_age(self) -> Optional[float]:
        if self.last_frame_time is None:
            return None
        return self._clock() - self.last_frame_time

    def is_stale(self) -> bool:
        age = self.frame_age()
        return self.state == ConnectionState.CONNECTED and (
            age is None or age > self.config.stale_frame_timeout_s)

    # ------------------------------------------------------------- internals
    def _warn(self, key: str, msg: str) -> None:
        if self._log is None:
            return
        suppressed = self._throttle.should_log(key)
        if suppressed is not None:
            extra = f' ({suppressed} similar suppressed)' if suppressed else ''
            self._log.warning(msg + extra)

    def _info(self, msg: str) -> None:
        if self._log is not None:
            self._log.info(msg)

    def _wait_before_retry(self, reason: str) -> None:
        self.last_error = reason
        delay = self.backoff.next_delay()
        self.next_retry_in_s = delay
        self.state = ConnectionState.RECONNECT_WAIT
        self._warn('reconnect', f'{self.config.describe()}: {reason}; retry #'
                   f'{self.backoff.attempts} in {delay:.1f}s')
        self._stop.wait(delay)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.state = ConnectionState.CONNECTING
            try:
                cap = self._open_fn(self.config)
            except Exception as exc:  # noqa: B902
                # Never echo the raw URL: backend exceptions can embed it with credentials.
                detail = str(exc) if '://' not in str(exc) else 'details withheld (contains URL)'
                self._wait_before_retry(f'open failed: {type(exc).__name__}: {detail}')
                continue
            if cap is None or not cap.isOpened():
                if cap is not None:
                    cap.release()
                self._wait_before_retry('could not open source')
                continue

            if self.backoff.attempts:
                self.reconnect_count += 1
            self.backoff.reset()
            self.state = ConnectionState.CONNECTED
            self.last_error = ''
            self._info(f'connected to {self.config.describe()}')
            ended = self._read_loop(cap)
            cap.release()
            if ended:
                self.state = ConnectionState.END_OF_FILE
                self._info('end of file reached and loop_file=false; capture stopped')
                return
            if not self._stop.is_set():
                self._wait_before_retry('stream read failed / disconnected')
        self.state = ConnectionState.STOPPED

    def _read_loop(self, cap: Any) -> bool:
        """Read until failure. Returns True only on a non-looping file EOF."""
        is_file = self.config.source_type == 'file'
        period = 0.0
        if is_file:
            fps = self.config.fps or _cap_fps(cap) or 30.0
            period = 1.0 / fps
        next_t = self._clock()
        consecutive_bad = 0
        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                if is_file:
                    if not self.config.loop_file:
                        return True
                    if _rewind(cap):
                        continue
                return False
            frame = normalize_frame(frame)
            if frame is None:
                self.invalid_frame_count += 1
                consecutive_bad += 1
                self._warn('bad_frame', 'source delivered an invalid frame; skipping')
                if consecutive_bad >= 30:
                    return False
                continue
            consecutive_bad = 0
            now = self._clock()
            self.last_frame_time = now
            self.resolution = (frame.shape[1], frame.shape[0])
            self.capture_rate.tick()
            with self._lock:
                self._seq += 1
                self._latest = (self._seq, frame, now)
                self._lock.notify_all()
            if self._on_frame is not None:
                try:
                    self._on_frame(frame, now)
                except Exception as exc:  # noqa: B902 - keep capturing
                    self._warn('callback', f'frame callback failed: {type(exc).__name__}: {exc}')
            if period:
                next_t += period
                delay = next_t - self._clock()
                if delay > 0:
                    self._stop.wait(delay)
                else:
                    next_t = self._clock()
        return False


def _cap_fps(cap: Any) -> float:
    try:
        import cv2
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        return fps if 0.5 < fps < 1000 else 0.0
    except Exception:  # noqa: B902
        return 0.0


def _rewind(cap: Any) -> bool:
    try:
        import cv2
        return bool(cap.set(cv2.CAP_PROP_POS_FRAMES, 0))
    except Exception:  # noqa: B902
        return False
