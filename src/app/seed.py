"""Demo-data seeding logic (D2) — pure orchestration over the service layer.

scripts/seed.py wires real Postgres/Silo/Redis/ffmpeg into SeedDeps and calls
seed_all(); unit tests inject in-memory fakes / autospec mocks, so no Docker
is needed here (tests/test_seed_unit.py).

Idempotent: every entity is get-or-create (users by email, videos by
owner+title, playlists by owner+title, reports by (video, reporter), ...), so
a re-run never duplicates. In production the entrypoint refuses to run
without DEMO_SEED=true (seed_allowed).
"""

from __future__ import annotations

import asyncio
import io
import os
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.core.storage import BUCKETS, StorageObject
from app.models import User, Video
from app.services import auth as auth_svc
from app.services import interactions as interactions_svc
from app.services import moderation as moderation_svc
from app.services import playlists as playlists_svc
from app.services import social as social_svc
from app.services import videos as videos_svc


class SeedError(Exception):
    """Seeding failed (transcode timeout, invalid generated file, ...)."""


def seed_allowed(app_env: str, demo_seed: str | None) -> bool:
    """Production seeding requires DEMO_SEED=true; dev is always allowed."""
    if app_env == "production":
        return (demo_seed or "").strip().lower() == "true"
    return True


# Fixed demo passwords — DEMO ONLY, never for real accounts (see README).
DEMO_ADMIN_PASSWORD = "DemoAdmin123"  # used only when ADMIN_PASSWORD is unset
DEMO_MOD_PASSWORD = "DemoMod123"
DEMO_USER_PASSWORD = "DemoUser123"


@dataclass(frozen=True)
class UserSpec:
    email: str
    password: str
    channel: str
    description: str
    role: str = "user"


@dataclass(frozen=True)
class VideoSpec:
    owner: str  # email of the owner UserSpec
    title: str
    description: str
    duration: int  # seconds
    source: str  # "testsrc" | "testsrc2" | "color=<name>"
    is_private: bool = False


@dataclass(frozen=True)
class ReportSpec:
    video_title: str
    reporter: str  # email
    reason: str
    comment: str | None
    resolution: str | None  # None → the report stays open ("new")


def demo_user_specs(admin_email: str, admin_channel: str, admin_password: str) -> list[UserSpec]:
    return [
        UserSpec(admin_email, admin_password, admin_channel,
                 "Administrator of this demo instance.", role="admin"),
        UserSpec("mod@example.com", DEMO_MOD_PASSWORD, "ModChannel",
                 "Community moderator (demo account).", role="moderator"),
        UserSpec("user1@example.com", DEMO_USER_PASSWORD, "ChannelUser1",
                 "Demo channel: travel vlogs."),
        UserSpec("user2@example.com", DEMO_USER_PASSWORD, "ChannelUser2",
                 "Demo channel: coding tutorials."),
        UserSpec("user3@example.com", DEMO_USER_PASSWORD, "ChannelUser3",
                 "Demo channel: cooking."),
    ]


U1, U2, U3, MOD = "user1@example.com", "user2@example.com", "user3@example.com", "mod@example.com"

DEMO_VIDEOS: list[VideoSpec] = [
    VideoSpec(U1, "Travel vlog: mountains at sunrise", "Hiking demo clip.", 6, "testsrc2"),
    VideoSpec(U1, "Travel vlog: the sea coast", "Waves and wind.", 4, "color=blue"),
    VideoSpec(U1, "Draft: video ideas (private)", "Private draft list.", 5, "color=gray",
              is_private=True),
    VideoSpec(U2, "Python: async basics in 8 minutes", "Async/await explained.", 8, "testsrc"),
    VideoSpec(U2, "Python: decorators explained", "Decorator patterns.", 7, "testsrc2"),
    VideoSpec(U2, "Screencast: debugging with prints", "Debugging session.", 6, "color=green"),
    VideoSpec(U2, "Members only: advanced async patterns", "Private, ACL-shared with user1.",
              5, "color=purple", is_private=True),
    VideoSpec(U3, "Cooking: quick pasta in 10 minutes", "Recipe demo.", 4, "testsrc"),
    VideoSpec(U3, "Cooking: sourdough bread", "Slow fermentation.", 8, "color=orange"),
    VideoSpec(U3, "Cooking: chocolate desserts", "Dessert ideas.", 5, "testsrc2"),
    VideoSpec(MOD, "Moderation: how reports work", "What moderators see.", 6, "color=black"),
    VideoSpec(U1, "Spammy upload (will be hidden)", "Reported as spam, hidden by moderation.",
              3, "testsrc"),
    VideoSpec(U2, "Misleading title (report dismissed)", "Reported, then dismissed.",
              3, "color=red"),
]

DEMO_SUBSCRIPTIONS: list[tuple[str, str]] = [  # (subscriber email, channel owner email)
    (U1, MOD), (U1, U2), (U2, U1), (U3, U1), (U3, MOD), (MOD, U3),
]

DEMO_PLAYLISTS: list[tuple[str, str, bool, list[str]]] = [  # (owner, title, private, videos)
    (U1, "Travel favorites", False,
     ["Travel vlog: mountains at sunrise", "Travel vlog: the sea coast"]),
    (U1, "Watch later", True,
     ["Python: async basics in 8 minutes", "Cooking: quick pasta in 10 minutes"]),
    (U3, "Recipes", False,
     ["Cooking: quick pasta in 10 minutes", "Cooking: sourdough bread",
      "Cooking: chocolate desserts"]),
]

DEMO_ACL: list[tuple[str, str]] = [  # (video title, granted user email)
    ("Members only: advanced async patterns", U1),
]

DEMO_REACTIONS: list[tuple[str, str, int]] = [  # (user email, video title, +1/-1)
    (U2, "Travel vlog: mountains at sunrise", 1),
    (U3, "Travel vlog: mountains at sunrise", 1),
    (U1, "Python: async basics in 8 minutes", 1),
    (U3, "Python: async basics in 8 minutes", -1),
    (MOD, "Moderation: how reports work", 1),
    (U2, "Cooking: quick pasta in 10 minutes", 1),
]

DEMO_COMMENTS: list[tuple[str, str, str, int | None]] = [  # (video, author, text, parent idx)
    ("Travel vlog: mountains at sunrise", U2, "Great views!", None),
    ("Travel vlog: mountains at sunrise", U3, "Adding this to my list.", 0),
    ("Travel vlog: mountains at sunrise", MOD, "Thanks for watching!", None),
    ("Python: async basics in 8 minutes", U1, "Very clear explanation.", None),
    ("Python: async basics in 8 minutes", U3, "The event loop part helped a lot.", 3),
]

DEMO_VIEWS: list[tuple[str, str | None, str | None]] = [  # (video, user email|None, guest fp)
    ("Travel vlog: mountains at sunrise", U2, None),
    ("Travel vlog: mountains at sunrise", U3, None),
    ("Travel vlog: mountains at sunrise", None, "guest-fp-1"),
    ("Travel vlog: mountains at sunrise", None, "guest-fp-1"),  # duplicate → not counted
    ("Travel vlog: mountains at sunrise", None, "guest-fp-2"),
    ("Python: async basics in 8 minutes", U1, None),
    ("Python: async basics in 8 minutes", None, "guest-fp-1"),
    ("Cooking: sourdough bread", U1, None),
]

DEMO_REPORTS: list[ReportSpec] = [
    ReportSpec("Spammy upload (will be hidden)", U3, "spam", "Looks like spam.", "hide"),
    ReportSpec("Misleading title (report dismissed)", U1, "misleading",
               "Title does not match.", "dismiss"),
    ReportSpec("Travel vlog: mountains at sunrise", U2, "other",
               "Just testing the report flow.", None),
]


def build_demo_ffmpeg_args(spec: VideoSpec, out_path: str) -> list[str]:
    """ffmpeg invocation for one demo clip — lavfi sources ONLY (testsrc /
    testsrc2 / solid color + sine tone); never an external input file."""
    if spec.source.startswith("color="):
        color = spec.source.split("=", 1)[1]
        video_src = f"color=c={color}:size=320x240:rate=10:duration={spec.duration}"
    elif spec.source == "testsrc2":
        video_src = f"testsrc2=size=320x240:rate=10:duration={spec.duration}"
    else:
        video_src = f"testsrc=size=320x240:rate=10:duration={spec.duration}"
    audio_src = f"sine=frequency={440 + (spec.duration * 37) % 400}:duration={spec.duration}"
    return [
        "ffmpeg", "-nostdin",
        "-f", "lavfi", "-i", video_src,
        "-f", "lavfi", "-i", audio_src,
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        "-y", out_path,
    ]


class SeedUserRepo(Protocol):
    async def get_by_email(self, email: str) -> User | None: ...
    async def get_by_channel_name(self, name: str) -> User | None: ...
    async def create_user(self, email: str, password_hash: str, channel_name: str) -> User: ...
    async def update_user(self, user: User) -> User: ...


class SeedVideoRepo(Protocol):
    async def create(self, video: Video) -> Video: ...
    async def get(self, video_id: uuid.UUID) -> Video | None: ...
    async def update(self, video: Video) -> Video: ...
    async def list_user_videos(
        self, user_id: uuid.UUID, *, limit: int, offset: int
    ) -> list[Video]: ...


class SeedAclRepo(Protocol):
    async def add(self, video_id: uuid.UUID, user_id: uuid.UUID) -> None: ...
    async def is_allowed(self, video_id: uuid.UUID, user_id: uuid.UUID) -> bool: ...


class SeedStorage(Protocol):
    def put_stream(
        self, bucket: str, key: str, path: str, content_type: str | None = None
    ) -> StorageObject: ...


@dataclass
class SeedDeps:
    """Everything seed_all needs — real implementations in scripts/seed.py,
    fakes/autospec mocks in tests/test_seed_unit.py."""

    user_repo: SeedUserRepo
    video_repo: SeedVideoRepo
    channel_repo: social_svc.ChannelRepo
    subscription_repo: social_svc.SubscriptionRepo
    acl_repo: SeedAclRepo
    view_repo: interactions_svc.ViewRepo
    reaction_repo: interactions_svc.ReactionRepo
    comment_repo: interactions_svc.CommentRepo
    playlist_repo: playlists_svc.PlaylistRepo
    report_repo: moderation_svc.ReportRepo
    storage: SeedStorage
    queue: videos_svc.TranscodeQueue
    ffmpeg: Callable[[list[str]], Awaitable[bool]]
    max_upload_size: int = 512 * 1024 * 1024
    transcode_timeout: float = 300.0
    poll_interval: float = 2.0
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


@dataclass
class SeedSummary:
    users: int = 0
    videos: int = 0
    ready: int = 0
    hidden: int = 0
    private: int = 0
    subscriptions: int = 0
    playlists: int = 0
    reactions: int = 0
    comments: int = 0
    views: int = 0
    reports_new: int = 0
    reports_resolved: int = 0


def _read_head(path: str, n: int) -> tuple[int, bytes]:
    """Blocking size+head read — always invoked via asyncio.to_thread."""
    with open(path, "rb") as fh:
        return os.path.getsize(path), fh.read(n)


async def _ensure_user(deps: SeedDeps, spec: UserSpec) -> User:
    user = await deps.user_repo.get_by_email(spec.email)
    if user is None:
        user = await auth_svc.register(deps.user_repo, spec.email, spec.password, spec.channel)
    if user.role != spec.role:
        user.role = spec.role
        user = await deps.user_repo.update_user(user)
    await deps.channel_repo.update_channel(user, channel_name=None, description=spec.description)
    return user


async def _wait_ready(deps: SeedDeps, video_id: uuid.UUID) -> Video:
    """Poll until the worker finishes the transcode job (ready/failed)."""
    waited = 0.0
    while True:
        video = await deps.video_repo.get(video_id)
        if video is None:
            raise SeedError(f"video {video_id} not found after enqueue")
        if video.status == "failed":
            raise SeedError(f"transcode failed for {video_id}: {video.failure_reason}")
        if video.status in ("ready", "hidden"):
            return video
        if waited >= deps.transcode_timeout:
            raise SeedError(f"transcode timeout for {video_id} after {waited:.0f}s")
        await deps.sleep(deps.poll_interval)
        waited += deps.poll_interval


async def _ensure_video(deps: SeedDeps, owner: User, spec: VideoSpec) -> Video:
    """One demo video through the NORMAL upload path: ffmpeg generates a
    clip, validate_upload checks it, it lands in the raw bucket, a Video row
    is created and the transcode job is enqueued (the worker produces the
    mp4 + thumbnail)."""
    existing = await deps.video_repo.list_user_videos(owner.id, limit=1000, offset=0)
    for v in existing:
        if v.title == spec.title and v.status != "deleted":
            return v
    video_id = uuid.uuid4()
    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, f"{video_id}.mp4")
        if not await deps.ffmpeg(build_demo_ffmpeg_args(spec, out_path)):
            raise SeedError(f"ffmpeg failed to generate {spec.title!r}")
        size, head = await asyncio.to_thread(_read_head, out_path, videos_svc.SNIFF_LEN)
        args = videos_svc.UploadArgs(
            filename=f"{video_id}.mp4",
            data=io.BytesIO(head),
            declared_size=size,
            max_size=deps.max_upload_size,
        )
        try:
            ext = videos_svc.validate_upload(args)
        except videos_svc.UploadRejected as exc:
            raise SeedError(f"generated clip {spec.title!r} rejected: {exc}") from exc
        raw_key = videos_svc.build_raw_key(video_id, ext)
        obj = await asyncio.to_thread(
            deps.storage.put_stream,
            BUCKETS["raw"],
            raw_key,
            out_path,
            videos_svc.content_type_for_ext(ext),
        )
        video = videos_svc.new_video(
            owner.id, spec.title, spec.description, raw_key, obj.size,
            is_private=spec.is_private,
        )
        video.id = video_id
        created = await deps.video_repo.create(video)
    await deps.queue.enqueue_transcode(created.id)
    return await _wait_ready(deps, created.id)


async def seed_all(
    deps: SeedDeps, *, admin_email: str, admin_channel: str, admin_password: str
) -> SeedSummary:
    summary = SeedSummary()

    users: dict[str, User] = {}
    for spec in demo_user_specs(admin_email, admin_channel, admin_password):
        users[spec.email] = await _ensure_user(deps, spec)
    summary.users = len(users)

    videos: dict[str, Video] = {}
    for vspec in DEMO_VIDEOS:
        videos[vspec.title] = await _ensure_video(deps, users[vspec.owner], vspec)

    for sub_email, channel_email in DEMO_SUBSCRIPTIONS:
        await social_svc.subscribe(
            deps.subscription_repo, users[sub_email], users[channel_email].id
        )
        summary.subscriptions += 1

    for owner_email, title, is_private, video_titles in DEMO_PLAYLISTS:
        owner = users[owner_email]
        existing = await deps.playlist_repo.list_channel_playlists(
            owner.id, viewer_id=owner.id, limit=100, offset=0
        )
        playlist = next((p for p in existing if p.title == title), None)
        if playlist is None:
            playlist = await deps.playlist_repo.create(
                playlists_svc.new_playlist(owner.id, title, is_private)
            )
        summary.playlists += 1
        items = await deps.playlist_repo.list_items_with_videos(playlist.id)
        have = {item.video_id for item, _video in items}
        position = len(items)
        for vt in video_titles:
            if videos[vt].id not in have:
                await deps.playlist_repo.add_item(playlist.id, videos[vt].id, position)
                position += 1

    for title, user_email in DEMO_ACL:
        await deps.acl_repo.add(videos[title].id, users[user_email].id)

    for user_email, title, value in DEMO_REACTIONS:
        await interactions_svc.set_reaction(
            deps.reaction_repo, users[user_email].id, videos[title].id, value
        )
        summary.reactions += 1

    comments_by_idx: dict[int, object] = {}
    for i, (title, author_email, text, parent_idx) in enumerate(DEMO_COMMENTS):
        video, author = videos[title], users[author_email]
        existing = await deps.comment_repo.list_for_video(video.id)
        match = next((c for c in existing if c.user_id == author.id and c.text == text), None)
        if match is not None:
            comments_by_idx[i] = match
            summary.comments += 1
            continue
        parent_id = comments_by_idx[parent_idx].id if parent_idx is not None else None
        comment = await interactions_svc.add_comment(
            deps.comment_repo, video.id, author.id, text, parent_id=parent_id
        )
        comments_by_idx[i] = comment
        summary.comments += 1

    for title, user_email, guest_fp in DEMO_VIEWS:
        user = users[user_email] if user_email else None
        fingerprint = f"user:{user.id}" if user is not None else (guest_fp or "guest")
        counted = await interactions_svc.register_view(
            deps.view_repo, videos[title].id, user, fingerprint
        )
        if counted:
            summary.views += 1

    moderator = users[MOD]
    for spec in DEMO_REPORTS:
        video, reporter = videos[spec.video_title], users[spec.reporter]
        if not await deps.report_repo.exists(video.id, reporter.id):
            await moderation_svc.submit_report(
                deps.report_repo, deps.video_repo, deps.acl_repo,
                video.id, reporter, spec.reason, spec.comment,
            )
    open_reports, _ = await deps.report_repo.list(status="new", limit=10000, offset=0)
    open_by_key = {(r.video_id, r.reporter_id): r for r in open_reports}
    for spec in DEMO_REPORTS:
        if spec.resolution is None:
            continue
        key = (videos[spec.video_title].id, users[spec.reporter].id)
        report = open_by_key.get(key)
        if report is not None:
            await moderation_svc.resolve_report(
                deps.report_repo, deps.video_repo, report.id,
                action=spec.resolution, note="demo seed", moderator=moderator,
            )

    # Re-fetch: a moderation "hide" changed one video's status meanwhile.
    for title, video in videos.items():
        fresh = await deps.video_repo.get(video.id)
        videos[title] = fresh
        summary.videos += 1
        if fresh.status == "hidden":
            summary.hidden += 1
        elif fresh.status == "ready":
            summary.ready += 1
        if fresh.is_private:
            summary.private += 1

    demo_video_ids = {v.id for v in videos.values()}
    all_reports, _ = await deps.report_repo.list(status=None, limit=10000, offset=0)
    demo_reports = [r for r in all_reports if r.video_id in demo_video_ids]
    summary.reports_new = sum(1 for r in demo_reports if r.status == "new")
    summary.reports_resolved = len(demo_reports) - summary.reports_new
    return summary
