"""Bounded exponential backoff used for camera/stream reconnection."""

import random
from typing import Callable, Optional


class ExponentialBackoff:
    """
    Delay generator: initial, initial*m, initial*m^2, ... capped at ``max_s``.

    ``jitter`` (0..1) randomises each delay downwards by up to that fraction so
    several clients reconnecting to the same RTSP server do not synchronise.
    """

    def __init__(self, initial_s: float, max_s: float, multiplier: float = 2.0,
                 jitter: float = 0.0, rng: Optional[Callable[[], float]] = None) -> None:
        if initial_s <= 0 or max_s < initial_s or multiplier < 1.0:
            raise ValueError('invalid backoff parameters')
        if not 0.0 <= jitter < 1.0:
            raise ValueError('jitter must be in [0, 1)')
        self._initial = initial_s
        self._max = max_s
        self._mult = multiplier
        self._jitter = jitter
        self._rng = rng or random.random
        self._attempts = 0

    @property
    def attempts(self) -> int:
        """Consecutive failures since the last ``reset()``."""
        return self._attempts

    def next_delay(self) -> float:
        """Register a failure and return how long to wait before retrying."""
        delay = min(self._max, self._initial * (self._mult ** self._attempts))
        self._attempts += 1
        if self._jitter:
            delay *= 1.0 - self._jitter * self._rng()
        return delay

    def reset(self) -> None:
        """Call after a successful connection."""
        self._attempts = 0
