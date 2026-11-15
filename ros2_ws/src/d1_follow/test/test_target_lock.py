import numpy as np
from d1_follow.target_lock import LockOutput, LockParams, State, TargetLock
from d1_follow.tracker import Track

W, H = 640, 480
ID_A = np.eye(8, dtype=np.float32)[0]          # the guest
ID_B = np.eye(8, dtype=np.float32)[1]          # a bystander
LOOKALIKE = (0.55 * ID_A + 0.835 * ID_B)        # cos(A, lookalike) ~0.55 < sim_accept
LOOKALIKE /= np.linalg.norm(LOOKALIKE)
NEAR = 0.75 * ID_A + 0.6614 * ID_B              # cos(A, near) 0.75: passes sim_accept
DRIFT = 0.6 * ID_A + 0.8 * ID_B                 # 0.60 to the seeds, 0.98 to NEAR


def T(tid, box, misses=0, hits=5):
    return Track(id=tid, box=np.array(box, np.float32), score=0.9, hits=hits, misses=misses)


class FakeEmbed:
    def __init__(self, identity):
        self.identity, self.calls, self.last = identity, 0, None

    def __call__(self, tracks):
        self.calls += 1
        self.last = [t.id for t in tracks]
        return np.stack([self.identity[t.id] for t in tracks]) if tracks else np.zeros((0, 8))


CENTER = [220, 40, 420, 470]     # big, centred: the guest at the laptop
SIDE = [520, 200, 600, 400]      # small, at the edge: a teammate
OVERLAP = [300, 40, 500, 470]    # crosses in front of CENTER (IoU ~0.43)


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
    events, t = [], 0.0
    for _ in range(15):
        out = lock.update(t, [], W, H, FakeEmbed({}))
        events.append(out.event)
        t += 0.1
    assert out.state == State.IDLE and lock.target_id is None
    assert events.count('lock_failed') == 1 and out.reason == 'no_person'


def test_lock_ambiguous_when_two_equal_people():
    lock = TargetLock(LockParams(lock_timeout_s=1.0))
    lock.request_lock(0.0)
    a, b = [100, 40, 300, 470], [340, 40, 540, 470]
    events, t = [], 0.0
    for _ in range(15):
        out = lock.update(t, [T(1, a), T(2, b)], W, H, FakeEmbed({1: ID_A, 2: ID_B}))
        events.append(out.event)
        t += 0.1
    assert out.state == State.IDLE
    assert events.count('lock_failed') == 1 and out.reason == 'ambiguous'


def test_locking_labels_dominant_track_as_candidate():
    lock = TargetLock(LockParams())
    lock.request_lock(0.0)
    out = lock.update(0.0, [T(1, CENTER), T(2, SIDE)], W, H, FakeEmbed({1: ID_A, 2: ID_B}))
    assert out.state == State.LOCKING and out.candidate_id == 1
    assert out.labels == {1: 'candidate', 2: 'unknown'}


def test_candidate_flicker_does_not_embed_every_frame():
    p = LockParams()
    lock, emb = TargetLock(p), FakeEmbed({1: ID_A, 2: ID_B})
    lock.request_lock(0.0)
    a, b = [100, 40, 300, 470], [340, 40, 540, 470]
    small_a, small_b = [100, 200, 300, 300], [340, 200, 540, 300]
    frames = [
        [T(1, a), T(2, small_b)],      # candidate 1
        [T(1, a), T(2, b)],            # tie: no candidate
        [T(1, small_a), T(2, b)],      # candidate 2
        [T(1, a), T(2, b)],            # tie: no candidate
    ]
    t, i = 0.0, 0
    while t <= p.lock_timeout_s + 0.5:
        out = lock.update(t, frames[i % 4], W, H, emb)
        t, i = t + 0.05, i + 1
    interval = p.lock_window_s / p.seed_size
    assert out.state == State.IDLE and out.reason == 'ambiguous'
    assert emb.calls <= int(p.lock_timeout_s / interval) + 2


def test_auto_lock():
    lock = TargetLock(LockParams(), auto_lock=True)
    out, _ = run(lock, [[T(1, CENTER)]] * 10, FakeEmbed({1: ID_A}))
    assert out.state == State.TRACKING


def test_zero_frame_size_is_skipped_instead_of_crashing():
    lock = TargetLock(LockParams(), auto_lock=True)
    out = lock.update(0.0, [T(1, CENTER)], 0, 0, FakeEmbed({1: ID_A}))
    assert out.state == State.IDLE and out.labels == {1: 'unknown'}
    out, _ = run(lock, [[T(1, CENTER)]] * 10, FakeEmbed({1: ID_A}))   # frame size known
    assert out.state == State.TRACKING


def test_occluder_that_steals_the_track_id_is_rejected_and_guest_reacquired():
    p = LockParams()
    lock, _, t = locked_lock()
    emb = FakeEmbed({1: ID_A, 2: ID_B})
    out, t = run(lock, [[T(1, CENTER), T(2, OVERLAP)]] * 5, emb, t0=t)
    assert out.state == State.TRACKING and emb.calls == 0   # merged crop decides nothing
    # they separate, but the tracker kept id 1 on the occluder; the guest is a new track 3
    swapped = FakeEmbed({1: ID_B, 3: ID_A})
    for _ in range(40):
        out = lock.update(t, [T(1, CENTER), T(3, SIDE)], W, H, swapped)
        t += 0.1
        if out.state != State.TRACKING:
            break
    assert out.state == State.LOST and out.reason == 'appearance_mismatch'
    assert swapped.calls <= p.reject_checks
    out, t = run(lock, [[T(1, CENTER), T(3, SIDE)]] * 25, swapped, t0=t)
    assert out.state == State.TRACKING and lock.target_id == 3
    assert out.labels == {1: 'unknown', 3: 'target'}


def test_crossing_without_an_id_swap_stays_tracking():
    lock, _, t = locked_lock()
    emb = FakeEmbed({1: ID_A, 2: ID_B})
    out, t = run(lock, [[T(1, CENTER), T(2, OVERLAP)]] * 5, emb, t0=t)
    out, t = run(lock, [[T(1, CENTER), T(2, SIDE)]] * 20, emb, t0=t)
    assert out.state == State.TRACKING and lock.target_id == 1
    assert out.labels == {1: 'target', 2: 'unknown'}


def test_gallery_entry_cannot_grant_identity_to_a_drifted_lookalike():
    lock, _, t = locked_lock()
    out, t = run(lock, [[T(1, CENTER)]] * 30, FakeEmbed({1: NEAR}), t0=t)
    assert out.state == State.TRACKING and len(lock._gallery) > lock._n_seeds
    lock.update(t, [], W, H, FakeEmbed({}))                  # target track dropped -> LOST
    out, t = run(lock, [[T(7, CENTER)]] * 50, FakeEmbed({7: DRIFT}), t0=t + 0.1)
    assert out.state == State.LOST
    out, _ = run(lock, [[T(8, CENTER)]] * 30, FakeEmbed({8: ID_A}), t0=t)
    assert out.state == State.TRACKING and lock.target_id == 8


def test_bystander_seen_beside_the_guest_is_never_reacquired():
    lock, _, t = locked_lock()               # track 2 was visible beside the guest
    out = lock.update(t, [T(2, SIDE)], W, H, FakeEmbed({2: ID_B}))
    assert out.state == State.LOST and out.reason == 'track_lost'
    near = FakeEmbed({2: NEAR})
    out, t = run(lock, [[T(2, CENTER)]] * 50, near, t0=t + 0.1)
    assert out.state == State.LOST and near.calls == 0   # not even worth an embedding
    out, _ = run(lock, [[T(2, CENTER), T(9, SIDE)]] * 30,
                 FakeEmbed({2: NEAR, 9: ID_A}), t0=t)
    assert out.state == State.TRACKING and lock.target_id == 9


def test_lost_caps_the_candidates_embedded_per_check():
    lock, _, t = locked_lock()
    lock.update(t, [], W, H, FakeEmbed({}))                  # target track dropped -> LOST
    boxes = [[0, 0, 40, 60], [0, 0, 300, 400], [0, 0, 100, 200], [0, 0, 200, 300]]
    tracks = [T(10 + i, b) for i, b in enumerate(boxes)]
    emb = FakeEmbed({tr.id: ID_B for tr in tracks})
    lock.update(t + 1.0, tracks, W, H, emb)
    assert emb.last == [11, 13, 12]          # the three biggest boxes, largest first


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
    assert out.reason == 'lost_timeout'
    lock2, _, t2 = locked_lock()
    lock2.release()
    assert lock2.state == State.IDLE and lock2.target_id is None
    out2 = lock2.update(t2, [T(1, CENTER)], W, H, FakeEmbed({1: ID_A}))
    assert out2.event == 'released' and out2.reason == 'released'


def test_lock_output_positional_field_order():
    o = LockOutput(State.TRACKING, None, None, {}, 0.8, None, None)
    assert o.state == State.TRACKING and o.target is None and o.candidate_id is None
    assert o.labels == {} and o.similarity == 0.8 and o.event is None and o.reason is None
    o = LockOutput(State.LOST, None, 3, {1: 'x'}, 0.4, 'lost', 'track_lost')
    assert (o.candidate_id, o.labels, o.similarity, o.event, o.reason) == (
        3, {1: 'x'}, 0.4, 'lost', 'track_lost')


def test_embed_row_count_mismatch_is_skipped():
    lock, _, t = locked_lock()
    bad = lambda tracks: np.zeros((0, 8), np.float32)  # noqa: E731
    out = lock.update(t + 2.0, [T(1, CENTER)], W, H, bad)
    assert out.state == State.TRACKING and out.similarity is None
    lock.update(t + 3.0, [], W, H, bad)                 # lost
    out = lock.update(t + 4.0, [T(5, CENTER)], W, H, bad)
    assert out.state == State.LOST and out.similarity is None


def test_embed_budget_while_tracking():
    lock, _, t = locked_lock()
    emb = FakeEmbed({1: ID_A, 2: ID_B})
    run(lock, [[T(1, CENTER), T(2, SIDE)]] * 50, emb, t0=t)     # 5 s at 10 Hz
    assert emb.calls <= 6


def test_embed_none_is_skipped():
    lock, _, t = locked_lock()
    out = lock.update(t + 2.0, [T(1, CENTER)], W, H, lambda tracks: None)
    assert out.state == State.TRACKING
