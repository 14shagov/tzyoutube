"""Queue client: enqueues transcode jobs via arq (production wiring).

Unit tests never touch this module's real Redis connection; the API
dependency get_queue is overridden with fakes.
"""

import uuid

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import Settings, get_settings


class ArqTranscodeQueue:
    """Enqueue transcode(video_id) on the shared arq pool.

    Review C1: the web app builds ONE instance in the lifespan (app.state)
    and passes the production pool in, so no per-request pool is created.
    """

    def __init__(self, pool: ArqRedis | None = None) -> None:
        self._pool = pool

    async def _get_pool(self) -> ArqRedis:
        if self._pool is None:
            settings = get_settings()
            self._pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        return self._pool

    async def enqueue_transcode(self, video_id: uuid.UUID) -> None:
        pool = await self._get_pool()
        await pool.enqueue_job("transcode", str(video_id))

    async def aclose(self) -> None:
        """Close the arq pool if this instance holds a live one."""
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None


async def build_queue(settings: Settings) -> ArqTranscodeQueue:
    """Create the process-wide queue once (review C1).

    Production connects the arq pool at startup (fail fast if Redis is
    down — compose healthchecks Redis first); dev stays lazy so unit tests
    never need Redis.
    """
    if settings.is_production:
        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        return ArqTranscodeQueue(pool)
    return ArqTranscodeQueue()
