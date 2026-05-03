"""Simple async token-bucket rate limiter, per upstream API.

We get one bucket per upstream so a burst against phish.net does not starve
phish.in (or vice versa). Tokens regenerate at a steady rate (``rps``) up to a
small burst capacity. ``acquire()`` waits as long as needed for one token.

This is per-instance only. Multiple containers will not coordinate; that's
fine for Phase 1. If we ever scale horizontally we will move to a Redis-backed
limiter, but this server is single-instance on nix1.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class _Snapshot:
    rps: float
    tokens_available: float
    last_call_ts: float | None


class TokenBucket:
    """One async token bucket.

    Args:
        rps: Steady-state requests-per-second target. Tokens regenerate at
            this rate.
        burst: Maximum tokens that can stack up while idle. Defaults to
            ``max(1, rps)`` rounded.
    """

    def __init__(self, rps: float, burst: float | None = None) -> None:
        if rps <= 0:
            raise ValueError("rps must be > 0")
        self.rps = rps
        self.capacity = burst if burst is not None else max(1.0, float(round(rps)))
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._last_call_wall: float | None = None
        self._lock = asyncio.Lock()

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rps)
            self._updated = now

    async def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        while True:
            async with self._lock:
                self._refill_locked()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    self._last_call_wall = time.time()
                    return
                deficit = 1.0 - self._tokens
                wait = deficit / self.rps
            # Sleep outside the lock so other coroutines can refill in parallel.
            await asyncio.sleep(wait)

    def snapshot(self) -> _Snapshot:
        """Return a fresh point-in-time view (does not consume a token)."""
        # Refill without blocking — read-only side effect.
        now = time.monotonic()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rps)
            self._updated = now
        return _Snapshot(
            rps=self.rps,
            tokens_available=round(self._tokens, 3),
            last_call_ts=self._last_call_wall,
        )
