import numpy as np
from d1_follow.tracker import IouTracker, iou


def B(*xyxy):
    return np.array([xyxy], dtype=np.float32)


def S(n=1):
    return np.ones(n, dtype=np.float32)


def test_iou():
    assert iou(np.array([0, 0, 10, 10]), np.array([0, 0, 10, 10])) == 1.0
    assert iou(np.array([0, 0, 10, 10]), np.array([20, 20, 30, 30])) == 0.0
    assert abs(iou(np.array([0, 0, 10, 10]), np.array([5, 0, 15, 10])) - 1 / 3) < 1e-6


def test_id_persists_and_confirms():
    t = IouTracker(min_hits=3)
    ids = []
    for k in range(4):
        tr = t.update(B(100 + 5 * k, 100, 200 + 5 * k, 400), S())
        assert len(tr) == 1
        ids.append(tr[0].id)
    assert len(set(ids)) == 1
    assert t.tracks[0].confirmed


def test_two_people_keep_ids_when_moving():
    t = IouTracker()
    boxes = np.array([[0, 0, 100, 300], [400, 0, 500, 300]], dtype=np.float32)
    first = {int(tr.box[0]) // 200: tr.id for tr in t.update(boxes, S(2))}
    for k in range(1, 6):
        moved = boxes + np.array([[10 * k, 0, 10 * k, 0], [-10 * k, 0, -10 * k, 0]], np.float32)
        tr = t.update(moved, S(2))
        by_side = {0 if tr_.box[0] < 250 else 2: tr_.id for tr_ in tr if tr_.visible}
        assert by_side == first


def test_track_dropped_after_max_misses_and_ids_not_reused():
    t = IouTracker(max_misses=2)
    a = t.update(B(0, 0, 100, 300), S())[0].id
    empty = np.zeros((0, 4), np.float32)
    for _ in range(2):
        assert len(t.update(empty, S(0))) == 1
    assert t.update(empty, S(0)) == []
    b = t.update(B(0, 0, 100, 300), S())[0].id
    assert b != a


def test_missed_track_is_not_visible():
    t = IouTracker()
    t.update(B(0, 0, 100, 300), S())
    tr = t.update(np.zeros((0, 4), np.float32), S(0))
    assert len(tr) == 1 and not tr[0].visible
