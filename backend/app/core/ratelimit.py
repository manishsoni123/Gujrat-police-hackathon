"""Tiny in-memory sliding-window limiters for /auth/login (single process).

Two limiters guard the login endpoint (CONTRACT §2.1 + Amendments 2026-09-05):

* per **IP**: every attempt counts (`LOGIN_RATE_LIMIT_PER_MIN` per minute); a successful login
  no longer clears the window, so a known low-privilege account cannot be used to unlock
  unlimited guesses at another account from the same address;
* per **username** (lower-cased): only *failed* attempts count — `USER_FAIL_LIMIT` failures in
  `USER_FAIL_WINDOW_S` seconds lock that account name for the rest of the window (`429`,
  audit `auth.login_failed` with `reason=rate_limited_user`); a successful login resets it.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

USER_FAIL_LIMIT = 5
USER_FAIL_WINDOW_S = 15 * 60.0


class RateLimiter:
    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._hits[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        return q

    def allow(self, key: str) -> bool:
        """Record one hit and return False when the window is already full."""
        now = time.monotonic()
        q = self._prune(key, now)
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True

    def blocked(self, key: str) -> bool:
        """Peek: is the window full? (no hit recorded)."""
        return len(self._prune(key, time.monotonic())) >= self.limit

    def record(self, key: str) -> None:
        """Record a hit without checking (used for failed attempts)."""
        self._prune(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)
