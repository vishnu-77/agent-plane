"""Cache + quota store.

A single interface used for rolling token-quota counters (and, optionally,
response caching). In-memory by default; Redis when STORAGE_BACKEND=postgres.
The interface keeps Redis a drop-in: the gateway never imports redis directly.
"""
from __future__ import annotations

import time
from typing import Protocol

from agent_plane.config import Settings


class CacheStore(Protocol):
    def incr_quota(self, key: str, amount: int, window_seconds: int) -> int:
        """Add ``amount`` to a rolling counter, return the new window total."""
        ...

    def get_quota(self, key: str) -> int: ...


class InMemoryCacheStore:
    def __init__(self) -> None:
        # key -> (window_start_epoch, total)
        self._counters: dict[str, tuple[float, int]] = {}

    def _now(self) -> float:
        return time.time()

    def incr_quota(self, key: str, amount: int, window_seconds: int) -> int:
        now = self._now()
        start, total = self._counters.get(key, (now, 0))
        if now - start >= window_seconds:
            start, total = now, 0
        total += amount
        self._counters[key] = (start, total)
        return total

    def get_quota(self, key: str) -> int:
        return self._counters.get(key, (0.0, 0))[1]


class RedisCacheStore:
    def __init__(self, url: str) -> None:
        import redis  # imported lazily so local mode needs no redis server

        self._redis = redis.Redis.from_url(url, decode_responses=True)

    def incr_quota(self, key: str, amount: int, window_seconds: int) -> int:
        rkey = f"quota:{key}"
        pipe = self._redis.pipeline()
        pipe.incrby(rkey, amount)
        pipe.expire(rkey, window_seconds, nx=True)
        total, _ = pipe.execute()
        return int(total)

    def get_quota(self, key: str) -> int:
        val = self._redis.get(f"quota:{key}")
        return int(val) if val else 0


def build_cache_store(settings: Settings) -> CacheStore:
    """Redis when asked for, memory otherwise.

    This used to follow STORAGE_BACKEND, so choosing Postgres demanded a Redis
    as well. A managed Postgres does not come with one, and quota counters are
    the only thing at stake here: authority, approvals and audit are always in
    SQL. CACHE_BACKEND=redis asks for it explicitly; "auto" still uses Redis
    when REDIS_URL points somewhere real.
    """
    if settings.uses_redis:
        return RedisCacheStore(settings.redis_url)
    return InMemoryCacheStore()
