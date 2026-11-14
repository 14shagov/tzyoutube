"""arq worker entrypoint: registers the real stage-2 tasks.

The module path `worker.main.WorkerSettings` is what docker-compose runs.
The task is a thin adapter: domain logic lives in app.services.transcode
(ADR-005). ffmpeg/ffprobe run as async subprocesses that are KILLED on
timeout (C2 — to_thread cannot kill a child process); blocking DB/S3 work
runs in threads.

DB access note: the adapter opens a short-lived engine per call on
purpose — asyncpg connections are loop-bound, and each asyncio.run()
creates a fresh loop; sharing the web app's global engine across loops
would raise "attached to a different loop".
"""

import asyncio
import uuid
from typing import ClassVar

from arq.connections import RedisSettings
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import Video
from app.services.transcode import (
    FFMPEG_TIMEOUT_SECONDS,
    PROBE_TIMEOUT_SECONDS,
    ProbeInfo,
    build_ffprobe_args,
    parse_ffprobe_json,
    run_transcode,
)


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


async def _ffmpeg_runner(args: list[str]) -> tuple[bool, str]:
    """Run ffmpeg as an async subprocess; KILL it on timeout (C2).

    The old to_thread(subprocess.run, timeout=...) could not kill the
    child: cancelling the thread left ffmpeg running and the arq
    job_timeout (300s) fired before the 600s subprocess timeout.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _out, err = await asyncio.wait_for(
            proc.communicate(), timeout=FFMPEG_TIMEOUT_SECONDS
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return False, f"ffmpeg killed after {FFMPEG_TIMEOUT_SECONDS}s timeout"
    return proc.returncode == 0, (err or b"").decode("utf-8", errors="replace")


async def _ffprobe(path: str) -> ProbeInfo:
    """Validate the produced file with ffprobe JSON (C3) — container,
    codecs and duration — instead of trusting ffmpeg's exit code alone."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        *build_ffprobe_args(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return ProbeInfo(ok=False, reason=f"ffprobe killed after {PROBE_TIMEOUT_SECONDS}s timeout")
    if proc.returncode != 0:
        tail = (err or b"").decode("utf-8", errors="replace")[-200:]
        return ProbeInfo(ok=False, reason=f"ffprobe exit {proc.returncode}: {tail}")
    return parse_ffprobe_json((out or b"").decode("utf-8", errors="replace"))


async def transcode(ctx: dict, video_id: str) -> None:
    """Enqueued by the web app as transcode(str(video_id)).

    Downloads the raw object, runs ffmpeg (480p + thumbnail), uploads the
    results and flips the video status (processing → ready | failed with
    failure_reason).
    """
    await run_transcode(
        _TaskRepo(),
        _TaskStorage(),
        uuid.UUID(video_id),
        _ffmpeg_runner,
        _ffprobe,
    )


class WorkerSettings:
    """arq worker settings (queue name defaults to arq)."""

    functions: ClassVar[list] = [transcode]
    on_startup = None
    on_shutdown = None
    # C2: arq's default job_timeout (300s) is shorter than one ffmpeg run
    # (600s) — the job was cancelled before the runner could record the
    # failure. Headroom lets the runner's own kill fire first.
    job_timeout = FFMPEG_TIMEOUT_SECONDS + 60
    # C2: bound concurrency explicitly (default 10 would spawn 10 ffmpeg
    # processes on one worker container).
    max_jobs = 2

    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
