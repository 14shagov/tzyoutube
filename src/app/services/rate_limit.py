"""Rate limiting with two backends: in-memory (tests/dev) and Redis (production).

ADR-002 keeps Redis as the production counter store; the in-memory variant
exists so unit tests run without Docker and so dev-without-redis still works.
"""

import time
from collections import defaultdict
from collections.abc import Callable
from datetime import timedelta

from redis.asyncio import Redis

from app.core.config import get_settings

# Re-exported for convenience: the protocol lives with the auth service.
from app.services.auth import RateLimiter as RateLimiterProtocol  # noqa: F401


class InMemoryRateLimiter:
    """Fixed-window counter. Process-local: fine for tests, not for prod."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._events: dict[str, list[float]] = defaultdict(list)
        self._clock = clock

    async def hit(self, key: str, limit: int, window: timedelta) -> bool:
        now = self._clock()
        events = self._events[key]
        cutoff = now - window.total_seconds()
        # Drop expired events (keeps list small without a background job).
        self._events[key] = events = [t for t in events if t > cutoff]
        if len(events) >= limit:
            return False
        events.append(now)
        return True

    async def check(self, key: str, limit: int, window: timedelta) -> bool:
        now = self._clock()
        cutoff = now - window.total_seconds()
        self._events[key] = [t for t in self._events[key] if t > cutoff]
        return len(self._events[key]) < limit


class RedisRateLimiter:
    """Sliding-window counter over a sorted set (production backend)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit(self, key: str, limit: int, window: timedelta) -> bool:
        now = time.time()
        cutoff = now - window.total_seconds()
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(key, "-inf", cutoff)
        pipe.zcard(key)
        results = await pipe.execute()
        count = results[1]
        if count >= limit:
            return False
        member = f"{now}:{id(self._redis)}"
        await self._redis.zadd(key, {member: now})
        await self._redis.expire(key, int(window.total_seconds()) + 1)
        return True

    async def check(self, key: str, limit: int, window: timedelta) -> bool:
        now = time.time()
        cutoff = now - window.total_seconds()
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(key, "-inf", cutoff)
        pipe.zcard(key)
        count = (await pipe.execute())[1]
        return count < limit


def get_rate_limiter() -> InMemoryRateLimiter | RedisRateLimiter:
    # Production uses Redis (per ADR-002); dev/tests default to in-memory.
    if get_settings().is_production:
        return RedisRateLimiter(Redis.from_url(get_settings().redis_url))
    return InMemoryRateLimiter()
