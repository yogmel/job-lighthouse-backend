"""In-memory sliding-window rate limit, per key (BE-049).

Kept in the process: no Redis or other service. That holds only because
Compose runs the Companies Service as **one** uvicorn process (no
``--workers``). More processes would each keep their own count. A restart
or deploy resets every count.

Not thread-safe: call it from the event loop only (async code), where each
``hit`` runs without interruption.
"""

import time
from collections import deque
from collections.abc import Callable, Hashable


class RateLimiter:
    """At most ``limit`` hits per key in any ``window_seconds``."""

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[Hashable, deque[float]] = {}

    def hit(self, key: Hashable) -> float | None:
        """Count a hit for ``key``.

        ``None`` if allowed. Over the limit, the hit isn't counted and the
        result is the seconds until the next one is allowed.
        """
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return hits[0] + self.window - now
        hits.append(now)
        self._prune(now)
        return None

    def _prune(self, now: float) -> None:
        """Drop keys with no hit left in the window, so memory stays bounded."""
        stale = [k for k, h in self._hits.items() if h[-1] <= now - self.window]
        for key in stale:
            del self._hits[key]
