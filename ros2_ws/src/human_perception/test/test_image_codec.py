from types import SimpleNamespace

import numpy as np
import pytest

from human_perception.image_codec import (bgr_to_image_fields, image_msg_to_bgr,
                                          MalformedImageError)


def _msg(arr, encoding, step=None, data=None):
    h, w = arr.shape[:2]
    row = arr.reshape(h, -1).shape[1] * arr.dtype.itemsize
    return SimpleNamespace(height=h, width=w, encoding=encoding, is_bigendian=0,
                           step=step if step is not None else row,
                           data=data if data is not None else arr.tobytes())


def _frame():
    rng = np.random.default_rng(1)
    return rng.integers(0, 255, (6, 8, 3), dtype=np.uint8)


def test_bgr_roundtrip():
    f = _frame()
    fields = bgr_to_image_fields(f)
    assert fields['encoding'] == 'bgr8' and fields['step'] == 24
    msg = SimpleNamespace(**fields)
    np.testing.assert_array_equal(image_msg_to_bgr(msg), f)


def test_rgb_is_converted_to_bgr():
    f = _frame()
    out = image_msg_to_bgr(_msg(f[:, :, ::-1].copy(), 'rgb8'))
    np.testing.assert_array_equal(out, f)


def test_mono_and_rgba_supported():
    mono = np.full((4, 5), 77, np.uint8)
    assert image_msg_to_bgr(_msg(mono, 'mono8')).shape == (4, 5, 3)
    rgba = np.zeros((4, 5, 4), np.uint8)
    assert image_msg_to_bgr(_msg(rgba, 'rgba8')).shape == (4, 5, 3)


def test_row_padding_is_respected():
    f = _frame()
    padded = np.zeros((6, 32), np.uint8)
    padded[:, :24] = f.reshape(6, 24)
    out = image_msg_to_bgr(_msg(f, 'bgr8', step=32, data=padded.tobytes()))
    np.testing.assert_array_equal(out, f)


@pytest.mark.parametrize('mutate', [
    lambda m: setattr(m, 'encoding', '32FC1'),
    lambda m: setattr(m, 'encoding', 'jpeg'),
    lambda m: setattr(m, 'data', m.data[:-1]),
    lambda m: setattr(m, 'step', 3),
    lambda m: setattr(m, 'width', 0),
    lambda m: setattr(m, 'height', 100000),
    lambda m: setattr(m, 'data', b''),
])
def test_malformed_images_raise(mutate):
    m = _msg(_frame(), 'bgr8')
    mutate(m)
    with pytest.raises(MalformedImageError):
        image_msg_to_bgr(m)


def test_bgr_to_fields_rejects_non_bgr():
    with pytest.raises(MalformedImageError):
        bgr_to_image_fields(np.zeros((4, 4), np.uint8))


def test_downscale_keeps_aspect_and_never_upscales():
    from human_perception.image_codec import downscale_to_width
    big = np.zeros((1080, 1920, 3), np.uint8)
    out = downscale_to_width(big, 960)
    assert out.shape == (540, 960, 3) and out.dtype == np.uint8
    small = np.zeros((480, 640, 3), np.uint8)
    assert downscale_to_width(small, 960) is small      # never upscale
    assert downscale_to_width(big, 0) is big            # 0 disables
