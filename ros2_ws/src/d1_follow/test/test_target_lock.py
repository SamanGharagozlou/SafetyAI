import numpy as np
from d1_follow.target_lock import LockParams, State, TargetLock
from d1_follow.tracker import Track

W, H = 640, 480
ID_A = np.eye(8, dtype=np.float32)[0]          # the guest
ID_B = np.eye(8, dtype=np.float32)[1]          # a bystander
LOOKALIKE = (0.55 * ID_A + 0.835 * ID_B)        # cos(A, lookalike) ~0.55 < sim_accept
LOOKALIKE /= np.linalg.norm(LOOKALIKE)


def T(tid, box, misses=0, hits=5):
    return Track(id=tid, box=np.array(box, np.float32), score=0.9, hits=hits, misses=misses)


class FakeEmbed:
    def __init__(self, identity):
        self.identity, self.calls = identity, 0

    def __call__(self, tracks):
        self.calls += 1
        return np.stack([self.identity[t.id] for t in tracks]) if tracks else np.zeros((0, 8))


CENTER = [220, 40, 420, 470]     # big, centred: the guest at the laptop
SIDE = [520, 200, 600, 400]      # small, at the edge: a teammate


def run(lock, frames, embed, t0=0.0, dt=0.1):
    out, t = None, t0
    for tracks in frames:
        out = lock.update(t, tracks, W, H, embed)
        t += dt
    return out, t


def locked_lock(identity=None):
    identity = identity or {1: ID_A, 2: ID_B}
    lock, emb = TargetLock(LockParams()), FakeEmbed(identity)
    lock.request_lock(0.0)
    out, t = run(lock, [[T(1, CENTER), T(2, SIDE)]] * 10, emb)
    assert out.state == State.TRACKING and lock.target_id == 1
    return lock, emb, t


def test_locks_onto_dominant_person_and_labels_others_unknown():
    lock, emb, t = locked_lock()
    out = lock.update(t, [T(1, CENTER), T(2, SIDE)], W, H, emb)
    assert out.labels == {1: 'target', 2: 'unknown'}
    assert out.target.id == 1


def test_lock_fails_with_no_person():
    lock = TargetLock(LockParams(lock_timeout_s=1.0))
    lock.request_lock(0.0)
    out, _ = run(lock, [[]] * 15, FakeEmbed({}))
    assert out.state == State.IDLE and lock.target_id is None


def test_lock_ambiguous_when_two_equal_people():
    lock = TargetLock(LockParams(lock_timeout_s=1.0))
    lock.request_lock(0.0)
    a, b = [100, 40, 300, 470], [340, 40, 540, 470]
    out, _ = run(lock, [[T(1, a), T(2, b)]] * 15, FakeEmbed({1: ID_A, 2: ID_B}))
    assert out.state == State.IDLE


def test_auto_lock():
    lock = TargetLock(LockParams(), auto_lock=True)
    out, _ = run(lock, [[T(1, CENTER)]] * 10, FakeEmbed({1: ID_A}))
    assert out.state == State.TRACKING


def test_occlusion_lost_then_reacquired_as_same_person_not_bystander():
    lock, _, t = locked_lock()
    # guest fully occluded: tracker drops track 1; bystander 2 stays in view
    out = lock.update(t, [T(2, SIDE)], W, H, FakeEmbed({2: ID_B}))
    assert out.state == State.LOST and out.target is None
    # bystander moves to centre while guest is hidden: must never become target
    emb = FakeEmbed({2: ID_B, 3: ID_A})
    out, t = run(lock, [[T(2, CENTER)]] * 20, emb, t0=t + 0.1)
    assert out.state == State.LOST and out.labels == {2: 'unknown'}
    # guest reappears with a new track id 3
    out, t = run(lock, [[T(2, SIDE), T(3, CENTER)]] * 15, emb, t0=t)
    assert out.state == State.TRACKING and lock.target_id == 3
    assert out.labels == {2: 'unknown', 3: 'target'}


def test_lookalike_never_accepted():
    lock, _, t = locked_lock()
    lock.update(t, [], W, H, FakeEmbed({}))           # lost
    emb = FakeEmbed({4: LOOKALIKE})
    out, _ = run(lock, [[T(4, CENTER)]] * 50, emb, t0=t + 0.1)
    assert out.state == State.LOST


def test_id_switch_detected_by_appearance():
    lock, _, t = locked_lock()
    # tracker wrongly hands id 1 to the bystander
    emb = FakeEmbed({1: ID_B})
    out, _ = run(lock, [[T(1, CENTER)]] * 30, emb, t0=t)
    assert out.state == State.LOST and out.reason == 'appearance_mismatch'


def test_lost_timeout_and_release():
    lock, _, t = locked_lock()
    lock.update(t, [], W, H, FakeEmbed({}))
    out = lock.update(t + 31.0, [], W, H, FakeEmbed({}))
    assert out.state == State.IDLE and out.event == 'lost_timeout'
    lock2, _, _ = locked_lock()
    lock2.release()
    assert lock2.state == State.IDLE and lock2.target_id is None


def test_embed_budget_while_tracking():
    lock, _, t = locked_lock()
    emb = FakeEmbed({1: ID_A, 2: ID_B})
    run(lock, [[T(1, CENTER), T(2, SIDE)]] * 50, emb, t0=t)     # 5 s at 10 Hz
    assert emb.calls <= 6


def test_embed_none_is_skipped():
    lock, _, t = locked_lock()
    out = lock.update(t + 2.0, [T(1, CENTER)], W, H, lambda tracks: None)
    assert out.state == State.TRACKING
