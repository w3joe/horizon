"""Explicit deterministic clock for offline closed-loop experiments."""

from __future__ import annotations

import threading


class ManualMonotonicClock:
    """Thread-safe nondecreasing nanosecond clock advanced by an experiment runner."""

    def __init__(self, initial_ns: int = 0):
        if isinstance(initial_ns, bool) or not isinstance(initial_ns, int) or initial_ns < 0:
            raise ValueError("initial_ns must be a nonnegative integer")
        self._now_ns = initial_ns
        self._lock = threading.Lock()

    def __call__(self) -> int:
        with self._lock:
            return self._now_ns

    def advance_ns(self, delta_ns: int) -> int:
        if isinstance(delta_ns, bool) or not isinstance(delta_ns, int) or delta_ns < 0:
            raise ValueError("delta_ns must be a nonnegative integer")
        with self._lock:
            self._now_ns += delta_ns
            return self._now_ns

    def set_ns(self, value_ns: int) -> int:
        if isinstance(value_ns, bool) or not isinstance(value_ns, int):
            raise ValueError("value_ns must be an integer")
        with self._lock:
            if value_ns < self._now_ns:
                raise ValueError("manual monotonic clock cannot move backward")
            self._now_ns = value_ns
            return self._now_ns
