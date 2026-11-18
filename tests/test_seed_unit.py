"""Unit tests for the demo-seed logic (app.seed) — no Docker, autospec fakes.

Real wiring (Postgres/Silo/Redis/ffmpeg) lives in scripts/seed.py; here every
dependency is an in-memory fake or an autospec mock, so `uv run pytest -q`
needs no containers.
"""

import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import create_autospec

import pytest
from minio import Minio

from app.core.storage import Storage
from app.models import (
    Comment,
    Playlist,
    PlaylistItem,
    Reaction,
    Subscription,
    User,
    Video,
    VideoAcl,
    VideoReport,
    VideoView,
)
from app.seed import (
    DEMO_VIDEOS,
    SeedDeps,
    SeedError,
    build_demo_ffmpeg_args,
    seed_all,
    seed_allowed,
)
from app.services import auth as auth_svc
from app.services.fakes import FakeUserRepo
from app.services.videos import TranscodeQueue

# Minimal valid mp4: ftyp box with an isom brand + padding.
MP4_BYTES = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 100


async def fake_ffmpeg(args: list[str]) -> bool:
    """Stand-in for the container ffmpeg: writes a tiny valid mp4."""
    Path(args[-1]).write_bytes(MP4_BYTES)
    return True


async def no_sleep(_seconds: float) -> None:
    return None


class FakeVideoRepo:
    def __init__(self) -> None:
        self.store: dict[uuid.UUID, Video] = {}

    async def create(self, video: Video) -> Video:
        self.store[video.id] = video
        return video

    async def get(self, video_id: uuid.UUID) -> Video | None:
        return self.store.get(video_id)

    async def update(self, video: Video) -> Video:
        self.store[video.id] = video
        return video

    async def list_user_videos(self, user_id, *, limit: int, offset: int):
        rows = [v for v in self.store.values() if v.user_id == user_id and v.status != "deleted"]
        return rows[offset : offset + limit]


class FakeChannelRepo:
    async def update_channel(self, user: User, *, channel_name, description) -> User:
        if channel_name is not None:
            user.channel_name = channel_name
        if description is not None:
            user.channel_description = description
        return user


class FakeSubscriptionRepo:
    def __init__(self) -> None:
        self.rows: set[tuple[uuid.UUID, uuid.UUID]] = set()

    async def is_subscribed(self, subscriber_id, channel_id) -> bool:
        return (subscriber_id, channel_id) in self.rows

    async def subscribe(self, subscriber_id, channel_id) -> None:
        self.rows.add((subscriber_id, channel_id))


class FakeAclRepo:
    def __init__(self) -> None:
        self.rows: set[tuple[uuid.UUID, uuid.UUID]] = set()

    async def is_allowed(self, video_id, user_id) -> bool:
        return (video_id, user_id) in self.rows

    async def add(self, video_id, user_id) -> None:
        self.rows.add((video_id, user_id))


class FakeViewRepo:
    def __init__(self) -> None:
        self.rows: list[tuple[uuid.UUID, str, datetime]] = []

    async def recent_view(self, video_id, fingerprint, since) -> bool:
        return any(v == video_id and f == fingerprint and t >= since for v, f, t in self.rows)

    async def add_view(self, video_id, user_id, fingerprint) -> None:
        self.rows.append((video_id, fingerprint, datetime.now()))

    async def increment_view_count(self, video_id) -> None:
        pass


class FakeReactionRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[uuid.UUID, uuid.UUID], int] = {}

    async def get(self, user_id, video_id):
        return self.rows.get((user_id, video_id))

    async def upsert(self, user_id, video_id, value) -> None:
        self.rows[(user_id, video_id)] = value

    async def delete(self, user_id, video_id) -> None:
        self.rows.pop((user_id, video_id), None)

    async def counts(self, video_id):
        vals = [v for (u, vid), v in self.rows.items() if vid == video_id]
        return vals.count(1), vals.count(-1)


class FakeCommentRepo:
    def __init__(self) -> None:
        self.rows: list[Comment] = []

    async def add(self, video_id, user_id, text, parent_id=None) -> Comment:
        c = Comment(video_id=video_id, user_id=user_id, text=text, parent_id=parent_id)
        c.id = uuid.uuid4()
        self.rows.append(c)
        return c

    async def list_for_video(self, video_id, limit=50, offset=0):
        return [c for c in self.rows if c.video_id == video_id][offset : offset + limit]


class FakePlaylistRepo:
    def __init__(self) -> None:
        self.playlists: dict[uuid.UUID, Playlist] = {}
        self.items: dict[uuid.UUID, list[PlaylistItem]] = {}

    async def create(self, playlist: Playlist) -> Playlist:
        self.playlists[playlist.id] = playlist
        self.items[playlist.id] = []
        return playlist

    async def get(self, playlist_id):
        return self.playlists.get(playlist_id)

    async def list_channel_playlists(self, channel_id, *, viewer_id, limit, offset):
        rows = [p for p in self.playlists.values() if p.user_id == channel_id]
        return rows[offset : offset + limit]

    async def add_item(self, playlist_id, video_id, position) -> PlaylistItem:
        item = PlaylistItem(playlist_id=playlist_id, video_id=video_id, position=position)
        self.items[playlist_id].append(item)
        return item

    async def list_items_with_videos(self, playlist_id):
        return [(i, None) for i in self.items.get(playlist_id, [])]

    async def max_position(self, playlist_id) -> int:
        items = self.items.get(playlist_id, [])
        return max((i.position for i in items), default=0)

    async def item_count(self, playlist_id) -> int:
        return len(self.items.get(playlist_id, []))


class FakeReportRepo:
    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, VideoReport] = {}

    async def add(self, report: VideoReport) -> VideoReport:
        self.rows[report.id] = report
        return report

    async def get(self, report_id):
        return self.rows.get(report_id)

    async def exists(self, video_id, reporter_id) -> bool:
        return any(r.video_id == video_id and r.reporter_id == reporter_id for r in self.rows.values())

    async def list(self, *, status, limit, offset):
        rows = list(self.rows.values())
        if status is not None:
            rows = [r for r in rows if r.status == status]
        return rows[offset : offset + limit], len(rows)

    async def mark_resolved(self, report_id, *, resolved_by, resolved_at, resolution, note):
        r = self.rows[report_id]
        r.status = "dismissed" if resolution == "dismiss" else "resolved"
        r.resolution = resolution
        r.resolved_by = resolved_by
        r.resolved_at = resolved_at
        return r


def _make_queue(video_repo: FakeVideoRepo, *, mark_ready: bool = True):
    """Autospec'd queue; simulates the worker marking videos ready."""
    queue = create_autospec(TranscodeQueue, instance=True)

    async def _enqueue(video_id: uuid.UUID) -> None:
        if mark_ready:
            v = video_repo.store[video_id]
            v.status = "ready"
            v.video_key = f"videos/{video_id}.mp4"
            v.thumbnail_key = f"thumbnails/{video_id}.jpg"
            v.duration_seconds = 5

    if mark_ready:
        queue.enqueue_transcode.side_effect = _enqueue
    return queue


def _make_deps(*, mark_ready: bool = True, transcode_timeout: float = 30.0):
    user_repo = FakeUserRepo()
    video_repo = FakeVideoRepo()
    queue = _make_queue(video_repo, mark_ready=mark_ready)
    client = create_autospec(Minio, instance=True)
    client.bucket_exists.return_value = True
    storage = Storage(client)
    deps = SeedDeps(
        user_repo=user_repo,
        video_repo=video_repo,
        channel_repo=FakeChannelRepo(),
        subscription_repo=FakeSubscriptionRepo(),
        acl_repo=FakeAclRepo(),
        view_repo=FakeViewRepo(),
        reaction_repo=FakeReactionRepo(),
        comment_repo=FakeCommentRepo(),
        playlist_repo=FakePlaylistRepo(),
        report_repo=FakeReportRepo(),
        storage=storage,
        queue=queue,
        ffmpeg=fake_ffmpeg,
        transcode_timeout=transcode_timeout,
        poll_interval=0,
        sleep=no_sleep,
    )
    return deps, user_repo, video_repo, queue, client


ADMIN = dict(admin_email="admin@example.com", admin_channel="AdminChannel",
             admin_password="DemoAdmin123")


class TestSeedGate:
    def test_production_requires_demo_flag(self) -> None:
        assert seed_allowed("production", None) is False
        assert seed_allowed("production", "false") is False
        assert seed_allowed("production", "true") is True
        assert seed_allowed("production", "TRUE") is True

    def test_dev_allows_without_flag(self) -> None:
        assert seed_allowed("dev", None) is True


class TestFfmpegArgs:
    def test_all_demo_videos_use_lavfi_sources_only(self) -> None:
        assert len(DEMO_VIDEOS) >= 12
        for spec in DEMO_VIDEOS:
            args = build_demo_ffmpeg_args(spec, "/tmp/out.mp4")
            assert args[0] == "ffmpeg"
            assert args.count("lavfi") == 2
            for i, a in enumerate(args):
                if a == "-i":
                    # lavfi source spec, never an external input file
                    assert "=" in args[i + 1]
                    assert not Path(args[i + 1]).exists()
            assert args[-1] == "/tmp/out.mp4"
            assert "-y" in args

    def test_sources_cover_testsrc_testsrc2_and_colors(self) -> None:
        sources = {s.source for s in DEMO_VIDEOS}
        assert "testsrc" in sources
        assert "testsrc2" in sources
        assert any(s.startswith("color=") for s in sources)
        assert len({s.title for s in DEMO_VIDEOS}) == len(DEMO_VIDEOS)


class TestSeedAll:
    async def test_creates_full_demo_dataset(self) -> None:
        deps, user_repo, video_repo, queue, _client = _make_deps()
        summary = await seed_all(deps, **ADMIN)

        assert summary.users == 5
        assert summary.videos == 13
        assert summary.ready == 12
        assert summary.hidden == 1
        assert summary.private == 2
        assert summary.subscriptions == 6
        assert summary.playlists == 3
        assert summary.reactions == 6
        assert summary.comments == 5
        assert summary.views == 7  # 8 view specs, one duplicate guest fingerprint
        assert summary.reports_new == 1
        assert summary.reports_resolved == 2

        hidden = [v for v in video_repo.store.values() if v.status == "hidden"]
        assert len(hidden) == 1 and "Spammy" in hidden[0].title

        acl_video = next(v for v in video_repo.store.values() if v.title.startswith("Members only"))
        user1 = await user_repo.get_by_email("user1@example.com")
        assert await deps.acl_repo.is_allowed(acl_video.id, user1.id)

        admin = await user_repo.get_by_email("admin@example.com")
        assert admin.role == "admin"
        assert admin.channel_name == "AdminChannel"
        assert auth_svc.verify_password("DemoAdmin123", admin.password_hash)
        assert auth_svc.verify_password("DemoUser123", user1.password_hash)
        mod = await user_repo.get_by_email("mod@example.com")
        assert mod.role == "moderator"

        # a reply (parent_id set) exists
        all_comments = [c for c in deps.comment_repo.rows if c.parent_id is not None]
        assert len(all_comments) == 2

    async def test_idempotent_second_run_creates_no_duplicates(self) -> None:
        deps, _user_repo, video_repo, queue, _client = _make_deps()
        first = await seed_all(deps, **ADMIN)
        second = await seed_all(deps, **ADMIN)

        assert len(video_repo.store) == 13
        assert queue.enqueue_transcode.call_count == 13  # no re-enqueue
        assert second.users == first.users == 5
        assert second.videos == first.videos == 13
        assert second.ready == first.ready == 12
        assert second.hidden == first.hidden == 1
        assert second.private == first.private == 2
        assert second.subscriptions == first.subscriptions
        assert second.playlists == first.playlists
        assert second.comments == first.comments
        assert second.reports_new == first.reports_new == 1
        assert second.reports_resolved == first.reports_resolved == 2
        assert second.views == 0  # all fingerprints deduped within 24h

    async def test_uses_normal_upload_and_transcode_path(self) -> None:
        deps, _user_repo, video_repo, queue, client = _make_deps()
        await seed_all(deps, **ADMIN)

        assert queue.enqueue_transcode.call_count == 13
        put_calls = client.put_object.call_args_list
        assert len(put_calls) == 13
        for call in put_calls:
            bucket, key = call.args[0], call.args[1]
            assert bucket == "raw"
            assert key.startswith("raw/") and key.endswith(".mp4")
            assert call.kwargs.get("content_type") == "video/mp4"
        for v in video_repo.store.values():
            assert v.raw_key == f"raw/{v.id}.mp4"
            assert v.status in ("ready", "hidden")

    async def test_admin_comes_from_env_parameters(self) -> None:
        deps, user_repo, _vr, _q, _c = _make_deps()
        await seed_all(deps, admin_email="root@example.com", admin_channel="RootChan",
                       admin_password="Sup3rSecret!")
        admin = await user_repo.get_by_email("root@example.com")
        assert admin is not None
        assert admin.role == "admin"
        assert admin.channel_name == "RootChan"
        assert auth_svc.verify_password("Sup3rSecret!", admin.password_hash)

    async def test_transcode_timeout_raises_seed_error(self) -> None:
        deps, _ur, _vr, _q, _c = _make_deps(mark_ready=False, transcode_timeout=0)
        with pytest.raises(SeedError, match="timeout"):
            await seed_all(deps, **ADMIN)
