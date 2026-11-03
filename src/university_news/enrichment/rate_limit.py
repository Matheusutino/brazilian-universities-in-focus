"""Client-side request pacing for rate-limited LLM endpoints."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import Generic, TypeVar

T = TypeVar("T")


class PacedPool(Generic[T]):
    """Hand out clients so that none exceeds its per-minute request limit.

    Each client (one per API key) gets evenly spaced request slots. A caller
    reserves the earliest free slot across all clients and sleeps until it, so
    several keys are used in rotation and bursts never exceed the limit. The
    pool is shared by the threads of one process; separate processes using the
    same key are not coordinated.
    """

    def __init__(
        self,
        clients: Sequence[T],
        requests_per_minute: float | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not clients:
            raise ValueError("PacedPool needs at least one client")
        if requests_per_minute is not None and requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self._clients = list(clients)
        self._interval = 60.0 / requests_per_minute if requests_per_minute else 0.0
        self._next_slot = [0.0] * len(self._clients)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._clients)

    def acquire(self) -> tuple[int, T, float]:
        """Return the client index, the client, and the seconds spent waiting."""
        with self._lock:
            now = self._clock()
            index = min(range(len(self._clients)), key=self._next_slot.__getitem__)
            slot = max(now, self._next_slot[index])
            self._next_slot[index] = slot + self._interval
        delay = slot - now
        if delay > 0:
            self._sleep(delay)
        return index, self._clients[index], delay

    def penalize(self, index: int, seconds: float) -> None:
        """Hold back one client after the server rejected it for rate limiting."""
        with self._lock:
            self._next_slot[index] = max(self._next_slot[index], self._clock() + seconds)
