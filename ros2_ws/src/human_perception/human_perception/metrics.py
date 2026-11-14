"""Lightweight performance metrics: rolling latency stats, rates, throttling."""

from collections import deque
import math
import threading
import time
from typing import Callable, Deque, Dict, Optional


Clock = Callable[[], float]


class RollingStats:
    """Mean / p95 / max over the most recent ``window`` samples (thread-safe)."""

    def __init__(self, window: int = 300) -> None:
        if window < 1:
            raise ValueError('window must be >= 1')
        self._samples: Deque[float] = deque(maxlen=window)
        self._lock = threading.Lock()

    def add(self, value: float) -> None:
        with self._lock:
            self._samples.append(float(value))

    def __len__(self) -> int:
        with self._lock:
            return len(self._samples)

    def summary(self) -> Dict[str, Optional[float]]:
        with self._lock:
            data = sorted(self._samples)
        if not data:
            return {'mean': None, 'p95': None, 'max': None, 'n': 0}
        return {
            'mean': sum(data) / len(data),
            'p95': percentile(data, 95.0, presorted=True),
            'max': data[-1],
            'n': len(data),
        }


def percentile(values, pct: float, presorted: bool = False) -> Optional[float]:
    """Return the nearest-rank percentile of ``values``; pure Python, stable for small n."""
    data = list(values) if presorted else sorted(values)
    if not data:
        return None
    if not 0.0 <= pct <= 100.0:
        raise ValueError('pct must be in [0, 100]')
    rank = max(1, math.ceil(pct / 100.0 * len(data)))
    return data[rank - 1]


class RateMeter:
    """Events per second over a sliding time window (thread-safe)."""

    def __init__(self, window_s: float = 5.0, clock: Clock = time.monotonic) -> None:
        self._window = window_s
        self._clock = clock
        self._events: Deque[float] = deque()
        self._lock = threading.Lock()
        self.total = 0

    def tick(self) -> None:
        now = self._clock()
        with self._lock:
            self._events.append(now)
            self.total += 1
            self._trim(now)

    def rate(self) -> float:
        now = self._clock()
        with self._lock:
            self._trim(now)
            n = len(self._events)
            if n < 2:
                return 0.0
            span = now - self._events[0]
        return (n - 1) / span if span > 0 else 0.0

    def _trim(self, now: float) -> None:
        while self._events and now - self._events[0] > self._window:
            self._events.popleft()


class LogThrottle:
    """Allow a message key at most once per ``period_s``; counts suppressed ones."""

    def __init__(self, period_s: float = 5.0, clock: Clock = time.monotonic) -> None:
        self._period = period_s
        self._clock = clock
        self._last: Dict[str, float] = {}
        self._suppressed: Dict[str, int] = {}
        self._lock = threading.Lock()

    def should_log(self, key: str) -> Optional[int]:
        """Return the suppressed count to report if allowed, else ``None``."""
        now = self._clock()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self._period:
                self._suppressed[key] = self._suppressed.get(key, 0) + 1
                return None
            self._last[key] = now
            return self._suppressed.pop(key, 0)


def fmt_ms(value: Optional[float]) -> str:
    """Format a seconds value as milliseconds, or ``n/a``."""
    return 'n/a' if value is None else f'{value * 1000.0:.1f}ms'
