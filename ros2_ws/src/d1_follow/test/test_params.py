from d1_follow.follow_controller import FollowParams
from d1_follow.params import clamp_follow_params


def test_clamps_over_limit_with_warnings():
    out, warnings = clamp_follow_params(FollowParams(max_vx=2.0, max_wz=3.0))
    assert out.max_vx == 0.5
    assert out.max_wz == 0.8
    assert len(warnings) == 2


def test_clamps_single_limit():
    out, warnings = clamp_follow_params(FollowParams(max_vx=0.3, max_wz=3.0))
    assert out.max_vx == 0.3
    assert out.max_wz == 0.8
    assert len(warnings) == 1


def test_within_caps_unchanged_without_warnings():
    p = FollowParams(max_vx=0.3, max_wz=0.5)
    out, warnings = clamp_follow_params(p)
    assert out == p
    assert warnings == []


def test_input_not_mutated():
    p = FollowParams(max_vx=2.0, max_wz=3.0)
    clamp_follow_params(p)
    assert p.max_vx == 2.0 and p.max_wz == 3.0
