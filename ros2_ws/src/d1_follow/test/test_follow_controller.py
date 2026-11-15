from d1_follow.follow_controller import FollowController, FollowParams

W, H = 640, 480
C = FollowController(FollowParams())


def box(cx, h):
    return [cx - 40, H / 2 - h / 2, cx + 40, H / 2 + h / 2]


def test_none_is_zero():
    assert C.compute(None, W, H) == (0.0, 0.0)


def test_centered_at_target_distance_is_zero():
    assert C.compute(box(W / 2, 0.6 * H), W, H) == (0.0, 0.0)


def test_turns_toward_person():
    _, wz_right = C.compute(box(W * 0.9, 0.6 * H), W, H)
    _, wz_left = C.compute(box(W * 0.1, 0.6 * H), W, H)
    assert wz_right < 0 < wz_left


def test_far_person_forward_close_person_never_reverse():
    vx_far, _ = C.compute(box(W / 2, 0.2 * H), W, H)
    vx_close, _ = C.compute(box(W / 2, 0.95 * H), W, H)
    assert vx_far > 0 and vx_close == 0.0


def test_clamped():
    p = FollowParams(k_x=100, k_yaw=100)
    vx, wz = FollowController(p).compute(box(W, 0.05 * H), W, H)
    assert vx == p.max_vx and wz == -p.max_wz
