"""Queue client: enqueues transcode jobs via arq (production wiring).

Unit tests never touch this module's real Redis connection; the API
dependency get_queue is overridden with fakes.
"""

import uuid

from arq import create_pool
from arq.connections import RedisSettings

from app.core.config import get_settings


class ArqTranscodeQueue:
    """Enqueue transcode(video_id) on the shared arq pool."""

    def __init__(self) -> None:
        self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            settings = get_settings()
            self._pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        return self._pool

    async def enqueue_transcode(self, video_id: uuid.UUID) -> None:
        pool = await self._get_pool()
        await pool.enqueue_job("transcode", str(video_id))
