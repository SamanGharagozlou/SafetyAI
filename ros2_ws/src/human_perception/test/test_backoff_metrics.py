import pytest

from human_perception.backoff import ExponentialBackoff
from human_perception.metrics import LogThrottle, percentile, RateMeter, RollingStats


def test_backoff_grows_exponentially_and_is_bounded():
    b = ExponentialBackoff(0.5, 4.0, 2.0)
    assert [b.next_delay() for _ in range(6)] == [0.5, 1.0, 2.0, 4.0, 4.0, 4.0]
    assert b.attempts == 6
    b.reset()
    assert b.attempts == 0 and b.next_delay() == 0.5


def test_backoff_jitter_only_shortens():
    b = ExponentialBackoff(1.0, 8.0, 2.0, jitter=0.5, rng=lambda: 1.0)
    assert b.next_delay() == 0.5


@pytest.mark.parametrize('args', [(0, 1), (2, 1), (1, 2, 0.9)])
def test_backoff_rejects_bad_params(args):
    with pytest.raises(ValueError):
        ExponentialBackoff(*args)


def test_percentile_nearest_rank():
    assert percentile([], 95) is None
    assert percentile([5.0], 95) == 5.0
    assert percentile(list(range(1, 101)), 95) == 95
    assert percentile(list(range(1, 101)), 50) == 50


def test_rolling_stats_window():
    s = RollingStats(window=3)
    assert s.summary()['mean'] is None
    for v in (100.0, 1.0, 2.0, 3.0):
        s.add(v)
    summary = s.summary()
    assert summary['n'] == 3 and summary['mean'] == 2.0 and summary['p95'] == 3.0


def test_rate_meter_with_fake_clock():
    t = [0.0]
    r = RateMeter(window_s=10.0, clock=lambda: t[0])
    for _ in range(11):
        r.tick()
        t[0] += 0.1
    t[0] -= 0.1
    assert r.rate() == pytest.approx(10.0)
    assert r.total == 11


def test_log_throttle_suppresses_and_reports_count():
    t = [0.0]
    th = LogThrottle(5.0, clock=lambda: t[0])
    assert th.should_log('k') == 0
    assert th.should_log('k') is None
    assert th.should_log('k') is None
    assert th.should_log('other') == 0
    t[0] = 6.0
    assert th.should_log('k') == 2
