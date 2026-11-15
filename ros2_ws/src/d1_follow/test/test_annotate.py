import numpy as np
from d1_follow.annotate import draw
from d1_follow.target_lock import LockOutput, State
from d1_follow.tracker import Track


def test_draw_colours_and_copy():
    img = np.zeros((480, 640, 3), np.uint8)
    tr = [Track(1, np.array([100, 100, 300, 400], np.float32), 0.9, 5, 0),
          Track(2, np.array([400, 100, 500, 300], np.float32), 0.9, 5, 0)]
    out = LockOutput(State.TRACKING, tr[0], None, {1: 'target', 2: 'unknown'}, 0.8, None, None)
    res = draw(img, tr, out, 0.2, -0.3)
    assert img.sum() == 0 and res.shape == img.shape
    assert tuple(res[250, 100]) == (0, 200, 0)          # target left edge green
    assert tuple(res[200, 400]) == (160, 160, 160)      # unknown left edge grey


def test_draw_skips_invisible_and_handles_no_target():
    img = np.zeros((480, 640, 3), np.uint8)
    tr = [Track(3, np.array([100, 100, 300, 400], np.float32), 0.9, 5, 2)]
    out = LockOutput(State.IDLE, None, None, {}, None, None, None)
    res = draw(img, tr, out, 0.0, 0.0)
    assert tuple(res[250, 100]) == (0, 0, 0)
