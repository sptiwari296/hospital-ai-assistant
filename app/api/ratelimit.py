"""Simple sliding-window rate limiter (in memory, per process).

Limits come from environment variables so tests and deployments can tune them:
  LOGIN_RATE_LIMIT  (per client IP, per minute, default 10)
  CHAT_RATE_LIMIT   (per user, per minute, default 20)
A multi-instance deployment would keep these counters in Redis instead.
"""
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException

WINDOW_SECONDS = 60


class SlidingWindowLimiter:
    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int) -> int | None:
        """Record one request. Returns seconds to wait if the limit is exceeded, else None."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > WINDOW_SECONDS:
                q.popleft()
            if len(q) >= limit:
                return int(WINDOW_SECONDS - (now - q[0])) + 1
            q.append(now)
            return None


limiter = SlidingWindowLimiter()


def enforce(bucket: str, key: str, env_var: str, default: int) -> None:
    retry = limiter.hit(f"{bucket}:{key}", int(os.getenv(env_var, default)))
    if retry is not None:
        raise HTTPException(429, "Too many requests. Please wait and try again.",
                            headers={"Retry-After": str(retry)})
