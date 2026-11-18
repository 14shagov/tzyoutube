#!/usr/bin/env python
"""Seed demo data (D2). Entrypoint of the `seed` compose profile.

All logic lives in app.seed (unit-tested without Docker); this module only
wires real Postgres/Silo/Redis/ffmpeg and enforces the DEMO_SEED gate:
in production it refuses to run unless DEMO_SEED=true is set.

Env: ADMIN_EMAIL / ADMIN_CHANNEL_NAME / ADMIN_PASSWORD override the first
admin account (deploy.sh passes a freshly generated password).
"""

import asyncio
import os
import sys

from app.api.auth import SqlUserRepo
from app.api.repos import (
    SqlAclRepo,
    SqlChannelRepo,
    SqlCommentRepo,
    SqlPlaylistRepo,
    SqlReactionRepo,
    SqlReportRepo,
    SqlSubscriptionRepo,
    SqlViewRepo,
)
from app.api.videos import SqlVideoRepo
from app.core.config import get_settings
from app.core.db import get_session_factory
from app.core.storage import get_storage
from app.seed import SeedDeps, SeedError, seed_all, seed_allowed
from app.worker.queue import build_queue


async def _ffmpeg(args: list[str]) -> bool:
    """Run ffmpeg in the container (the image ships it); True on rc=0."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate()
    if proc.returncode != 0:
        print(f"seed: ffmpeg failed: {err.decode(errors='replace')[-500:]}", file=sys.stderr)
        return False
    return True


async def _main() -> int:
    settings = get_settings()
    if not seed_allowed(settings.app_env, os.environ.get("DEMO_SEED")):
        print("seed: refusing to seed in production without DEMO_SEED=true", file=sys.stderr)
        return 1

    storage = get_storage()
    storage.ensure_buckets()
    queue = await build_queue(settings)
    try:
        async with get_session_factory()() as session:
            deps = SeedDeps(
                user_repo=SqlUserRepo(session),
                video_repo=SqlVideoRepo(session),
                channel_repo=SqlChannelRepo(session),
                subscription_repo=SqlSubscriptionRepo(session),
                acl_repo=SqlAclRepo(session),
                view_repo=SqlViewRepo(session),
                reaction_repo=SqlReactionRepo(session),
                comment_repo=SqlCommentRepo(session),
                playlist_repo=SqlPlaylistRepo(session),
                report_repo=SqlReportRepo(session),
                storage=storage,
                queue=queue,
                ffmpeg=_ffmpeg,
                max_upload_size=settings.max_upload_size,
            )
            summary = await seed_all(
                deps,
                admin_email=os.environ.get("ADMIN_EMAIL", "admin@example.com"),
                admin_channel=os.environ.get("ADMIN_CHANNEL_NAME", "AdminChannel"),
                admin_password=os.environ.get("ADMIN_PASSWORD", "DemoAdmin123"),
            )
    finally:
        await queue.aclose()

    print(
        f"seed: done — users={summary.users} videos={summary.videos} "
        f"(ready={summary.ready} hidden={summary.hidden} private={summary.private}) "
        f"subs={summary.subscriptions} playlists={summary.playlists} "
        f"reactions={summary.reactions} comments={summary.comments} views={summary.views} "
        f"reports(new={summary.reports_new} resolved={summary.reports_resolved})"
    )
    return 0


def main() -> int:
    try:
        return asyncio.run(_main())
    except SeedError as exc:
        print(f"seed: ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
