"""arq worker entrypoint: registers the real stage-2 tasks.

The module path `worker.main.WorkerSettings` is what docker-compose runs.
The task is a thin adapter: domain logic lives in app.services.transcode
(ADR-005). Blocking work (minio-py, ffmpeg subprocess) runs in threads.

DB access note: the adapter opens a short-lived engine per call on
purpose — asyncpg connections are loop-bound, and each asyncio.run()
creates a fresh loop; sharing the web app's global engine across loops
would raise "attached to a different loop".
"""

import asyncio
import subprocess
import uuid
from typing import ClassVar

from arq.connections import RedisSettings
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import Video
from app.services.transcode import run_transcode


def _db_call(coro_factory):
    """Run one async DB operation on its own throwaway engine+loop."""

    async def _inner():
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await coro_factory(session)
        finally:
            await engine.dispose()

    return asyncio.run(_inner())


class _TaskRepo:
    """Sync TranscodeRepo facade over async SQLAlchemy (per-call engines)."""

    def get(self, video_id: uuid.UUID):
        async def _query(session):
            stmt = select(Video).where(Video.id == video_id)
            return (await session.execute(stmt)).scalar_one_or_none()

        return _db_call(_query)

    def set_status(self, video_id: uuid.UUID, status: str, **fields) -> None:
        async def _update(session):
            stmt = (
                update(Video).where(Video.id == video_id).values(status=status, **fields)
            )
            await session.execute(stmt)
            await session.commit()

        _db_call(_update)


class _TaskStorage:
    """Sync TranscodeStorage over the minio-py client (Silo)."""

    def __init__(self) -> None:
        from app.core.storage import get_storage

        self._client = get_storage()._s3

    def download_to(self, bucket: str, key: str, dest_path: str) -> None:
        self._client.fget_object(bucket, key, dest_path)

    def upload_file(self, bucket: str, key: str, path: str) -> None:
        self._client.fput_object(bucket, key, path)


def _ffmpeg_runner(args: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(
        ["ffmpeg", *args], capture_output=True, text=True, timeout=600, check=False
    )
    return proc.returncode == 0, proc.stderr


def _duration_probe(path: str) -> float | None:
    from app.services.transcode import parse_duration

    proc = subprocess.run(
        ["ffmpeg", "-i", path], capture_output=True, text=True, timeout=60, check=False
    )
    return parse_duration(proc.stderr)


async def transcode(ctx: dict, video_id: str) -> None:
    """Enqueued by the web app as transcode(str(video_id)).

    Downloads the raw object, runs ffmpeg (480p + thumbnail), uploads the
    results and flips the video status (processing → ready | failed).
    """
    await asyncio.to_thread(
        run_transcode,
        _TaskRepo(),
        _TaskStorage(),
        uuid.UUID(video_id),
        _ffmpeg_runner,
        _duration_probe,
    )


class WorkerSettings:
    """arq worker settings (queue name defaults to arq)."""

    functions: ClassVar[list] = [transcode]
    on_startup = None
    on_shutdown = None

    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
