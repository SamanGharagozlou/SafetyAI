import math

from d1_follow.relay_core import RelayGate, RelayParams


def tracking_gate(**kw):
    """Armed gate with a fresh TRACKING state and a fresh command at t=0."""
    g = RelayGate(RelayParams(**kw))
    g.arm(0.0)
    g.on_state(0.0, 'TRACKING')
    g.on_cmd(0.0, 0.2, 0.1)
    return g


def test_disarmed_is_silent_even_when_tracking():
    g = RelayGate(RelayParams())
    g.on_state(0.0, 'TRACKING')
    g.on_cmd(0.0, 0.2, 0.1)
    assert g.output(0.1) is None
    assert g.reason(0.1) == 'disarmed'


def test_armed_tracking_forwards_the_command():
    g = tracking_gate()
    assert g.output(0.1) == (0.2, 0.1)
    assert g.reason(0.1) == 'ok'


def test_command_is_capped_and_never_reverses():
    g = tracking_gate(max_vx=0.3, max_wz=0.5)
    g.on_cmd(0.1, 0.5, -0.8)
    assert g.output(0.1) == (0.3, -0.5)
    g.on_cmd(0.2, -1.0, 0.0)
    assert g.output(0.2) == (0.0, 0.0)


def test_non_finite_values_become_zero():
    g = tracking_gate()
    g.on_cmd(0.1, math.nan, math.inf)
    assert g.output(0.1) == (0.0, 0.0)


def test_not_tracking_stops_once_then_goes_silent():
    g = tracking_gate()
    assert g.output(0.1) == (0.2, 0.1)
    for state in ('LOST', 'IDLE', 'LOCKING'):
        g2 = tracking_gate()
        g2.output(0.1)
        g2.on_state(0.2, state)
        assert g2.output(0.2) == (0.0, 0.0)
        assert g2.output(0.3) is None
        assert g2.reason(0.3) == 'state ' + state


def test_stale_command_stops():
    g = tracking_gate(cmd_timeout_s=0.5)
    assert g.output(0.4) == (0.2, 0.1)
    assert g.output(0.6) == (0.0, 0.0)
    assert g.reason(0.6) == 'stale cmd'
    assert g.output(0.7) is None


def test_stale_state_stops():
    g = tracking_gate(state_timeout_s=1.5, cmd_timeout_s=10.0)
    assert g.output(1.0) == (0.2, 0.1)
    assert g.output(1.6) == (0.0, 0.0)
    assert g.reason(1.6) == 'stale state'


def test_no_command_yet_is_blocked():
    g = RelayGate(RelayParams())
    g.arm(0.0)
    g.on_state(0.0, 'TRACKING')
    assert g.output(0.1) is None
    assert g.reason(0.1) == 'stale cmd'


def test_disarm_stops_once():
    g = tracking_gate()
    g.output(0.1)
    g.disarm()
    assert g.output(0.2) == (0.0, 0.0)
    assert g.output(0.3) is None


def test_arm_expires():
    g = tracking_gate(arm_timeout_s=10.0, cmd_timeout_s=100.0, state_timeout_s=100.0)
    assert g.output(5.0) == (0.2, 0.1)
    assert g.output(10.5) == (0.0, 0.0)
    assert not g.armed
    assert g.output(10.6) is None


def test_arm_timeout_zero_never_expires():
    g = tracking_gate(arm_timeout_s=0.0, cmd_timeout_s=1e6, state_timeout_s=1e6)
    assert g.output(1e5) == (0.2, 0.1)


def test_rearm_after_not_tracking_resumes_only_when_tracking_again():
    g = tracking_gate()
    g.output(0.1)
    g.on_state(0.2, 'LOST')
    g.output(0.2)
    g.on_state(0.3, 'TRACKING')
    g.on_cmd(0.3, 0.1, 0.0)
    assert g.output(0.3) == (0.1, 0.0)


def test_caps_are_bounded_by_absolute_ceilings():
    p = RelayParams(max_vx=5.0, max_wz=-5.0)
    g = RelayGate(p)
    g.arm(0.0)
    g.on_state(0.0, 'TRACKING')
    g.on_cmd(0.0, 5.0, 5.0)
    vx, wz = g.output(0.0)
    assert vx <= 0.5 and wz <= 0.8
