"""
Convert between ``sensor_msgs/Image``-like objects and numpy BGR arrays.

This intentionally avoids ``cv_bridge``: on ROS 2 Humble the binary cv_bridge
is built against the distro numpy (1.21) and frequently breaks inside a
virtual environment that carries a newer numpy for Ultralytics/PyTorch.  The
conversion below only touches message *fields* (duck typing), so it is also
testable without ROS.

Every input is validated; anything inconsistent raises ``MalformedImageError``
instead of producing a garbage tensor.
"""

from typing import Any, Dict, Tuple

import numpy as np

# encoding -> (channels, dtype)
_LAYOUTS: Dict[str, Tuple[int, Any]] = {
    'bgr8': (3, np.uint8),
    'rgb8': (3, np.uint8),
    'bgra8': (4, np.uint8),
    'rgba8': (4, np.uint8),
    'mono8': (1, np.uint8),
    '8uc1': (1, np.uint8),
    '8uc3': (3, np.uint8),
    'mono16': (1, np.uint16),
    'yuv422': (2, np.uint8),          # UYVY
    'uyvy': (2, np.uint8),
    'yuv422_yuy2': (2, np.uint8),     # YUYV
    'yuyv': (2, np.uint8),
}

SUPPORTED_ENCODINGS = tuple(sorted(_LAYOUTS))
MAX_DIMENSION = 16384


class MalformedImageError(ValueError):
    """Raised when an image message cannot be decoded safely."""


def image_msg_to_bgr(msg: Any) -> np.ndarray:
    """Decode an Image message into a contiguous HxWx3 uint8 BGR array."""
    import cv2  # local import keeps module importable for light tooling

    encoding = str(getattr(msg, 'encoding', '')).lower()
    if encoding not in _LAYOUTS:
        raise MalformedImageError(
            f'unsupported encoding {encoding!r}; supported: {", ".join(SUPPORTED_ENCODINGS)}. '
            'Compressed topics must be republished as raw (image_transport republish).')
    channels, dtype = _LAYOUTS[encoding]
    height, width, step = int(msg.height), int(msg.width), int(msg.step)
    if not (0 < height <= MAX_DIMENSION and 0 < width <= MAX_DIMENSION):
        raise MalformedImageError(f'invalid image size {width}x{height}')
    itemsize = np.dtype(dtype).itemsize
    row_bytes = width * channels * itemsize
    if step < row_bytes:
        raise MalformedImageError(f'step {step} smaller than row size {row_bytes}')
    data = msg.data
    buf = np.frombuffer(bytes(data) if not isinstance(data, (bytes, bytearray, memoryview))
                        else data, dtype=np.uint8)
    if buf.size < step * height:
        raise MalformedImageError(
            f'data length {buf.size} < step*height {step * height} ({encoding} {width}x{height})')

    rows = buf[:step * height].reshape(height, step)[:, :row_bytes]
    if itemsize > 1:
        byteorder = '>' if getattr(msg, 'is_bigendian', 0) else '<'
        pixels = np.ascontiguousarray(rows).view(np.dtype(dtype).newbyteorder(byteorder))
        pixels = pixels.reshape(height, width, channels).astype(dtype)
    else:
        pixels = rows.reshape(height, width, channels)

    if encoding in ('bgr8', '8uc3'):
        out = pixels
    elif encoding == 'rgb8':
        out = cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR)
    elif encoding == 'bgra8':
        out = cv2.cvtColor(pixels, cv2.COLOR_BGRA2BGR)
    elif encoding == 'rgba8':
        out = cv2.cvtColor(pixels, cv2.COLOR_RGBA2BGR)
    elif encoding in ('mono8', '8uc1'):
        out = cv2.cvtColor(pixels, cv2.COLOR_GRAY2BGR)
    elif encoding == 'mono16':
        out = cv2.cvtColor((pixels >> 8).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    elif encoding in ('yuv422', 'uyvy'):
        out = cv2.cvtColor(pixels, cv2.COLOR_YUV2BGR_UYVY)
    else:  # yuyv / yuv422_yuy2
        out = cv2.cvtColor(pixels, cv2.COLOR_YUV2BGR_YUY2)
    return np.ascontiguousarray(out)


def bgr_to_image_fields(frame: np.ndarray) -> Dict[str, Any]:
    """Return Image field values for a HxWx3 uint8 BGR array."""
    if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
        raise MalformedImageError('expected an HxWx3 uint8 BGR frame')
    frame = np.ascontiguousarray(frame)
    height, width = frame.shape[:2]
    return {
        'height': height,
        'width': width,
        'encoding': 'bgr8',
        'is_bigendian': 0,
        'step': width * 3,
        'data': frame.tobytes(),
    }


def fill_image_msg(msg: Any, frame: np.ndarray) -> Any:
    """Populate an existing ``sensor_msgs/Image`` from a BGR frame."""
    for key, value in bgr_to_image_fields(frame).items():
        setattr(msg, key, value)
    return msg


def downscale_to_width(frame: np.ndarray, width: int) -> np.ndarray:
    """Return ``frame`` resized to ``width`` (aspect kept); never upscales; 0 = no-op."""
    import cv2

    if not width or frame.shape[1] <= width:
        return frame
    height = max(1, int(round(frame.shape[0] * width / frame.shape[1])))
    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
