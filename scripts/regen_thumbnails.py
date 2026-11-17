"""Re-enqueue transcode for every ready video (ADR-009 follow-up).

Existing uploads were transcoded with the old fixed `-ss 1` thumbnail
offset. Re-running the transcode job regenerates video + thumbnail with
the new `thumbnail_offset_seconds()` (10% of duration, capped at 1 s).

Runs inside the `seed`-style compose profile against the real DB + Redis:
    docker compose --profile tools run --rm tools \
        python scripts/regen_thumbnails.py
"""

import asyncio
import sys
import uuid

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.models import Video
from app.worker.queue import ArqTranscodeQueue


async def main() -> int:
    settings = get_settings()
    if not settings.is_production:
        print("refusing to run outside production (set APP_ENV=production)", file=sys.stderr)
        return 1
    session_factory = get_session_factory()
    queue = ArqTranscodeQueue()
    count = 0
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(Video.id).where(Video.status == "ready", Video.raw_key.is_not(None))
            )
            ids: list[uuid.UUID] = list(result.scalars().all())
        for video_id in ids:
            await queue.enqueue_transcode(video_id)
            count += 1
        print(f"enqueued {count} transcode jobs for thumbnail regeneration")
    finally:
        await queue.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
