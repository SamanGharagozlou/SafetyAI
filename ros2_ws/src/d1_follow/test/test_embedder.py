import numpy as np
from d1_follow.embedder import HistogramEmbedder, make_embedder


def person(color_top, color_bottom, h=200, w=80):
    img = np.zeros((h, w, 3), np.uint8)
    img[: h // 2] = color_top
    img[h // 2:] = color_bottom
    return img


def scene():
    img = np.zeros((300, 400, 3), np.uint8)
    img[50:250, 10:90] = person((0, 0, 200), (200, 0, 0))      # red top, blue bottom (BGR)
    img[50:250, 150:230] = person((0, 0, 200), (200, 0, 0))    # same clothes
    img[50:250, 300:380] = person((0, 200, 0), (0, 200, 200))  # green top, yellow bottom
    boxes = np.array([[10, 50, 90, 250], [150, 50, 230, 250], [300, 50, 380, 250]], np.float32)
    return img, boxes


def test_histogram_shapes_and_norm():
    img, boxes = scene()
    f = HistogramEmbedder().embed(img, boxes)
    assert f.shape[0] == 3 and f.dtype == np.float32
    assert np.allclose(np.linalg.norm(f, axis=1), 1.0, atol=1e-5)


def test_histogram_discriminates():
    img, boxes = scene()
    f = HistogramEmbedder().embed(img, boxes)
    assert f[0] @ f[1] > 0.95
    assert f[0] @ f[2] < 0.5


def test_degenerate_and_empty():
    img, _ = scene()
    e = HistogramEmbedder()
    assert e.embed(img, np.zeros((0, 4), np.float32)).shape[0] == 0
    f = e.embed(img, np.array([[500, 500, 600, 600]], np.float32))   # fully outside
    assert np.allclose(f, 0)


def test_make_embedder_falls_back():
    msgs = []
    e = make_embedder('osnet', 'cpu', log=msgs.append)
    assert e.name == 'osnet' or (e.name == 'histogram' and msgs)
    assert make_embedder('histogram', 'cpu').name == 'histogram'
