"""HTTP layer for videos: upload endpoint (stage 2B).

Auth via get_current_user; storage/queue/repo injected as dependencies
so tests can override them with fakes.
"""

import uuid

from fastapi import APIRouter, Cookie, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi import status as http_status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import SqlUserRepo, get_current_user, get_user_repo
from app.core.config import Settings, get_settings
from app.core.db import get_db_session
from app.core.storage import BUCKETS, Storage
from app.models import User, Video
from app.services import auth as auth_service
from app.services import videos as videos_svc

router = APIRouter(prefix="/api/videos", tags=["videos"])


async def _optional_user(
    repo: SqlUserRepo = Depends(get_user_repo),
    session_id: str | None = Cookie(default=None),
) -> User | None:
    """Guest-tolerant current user: None instead of 401 (feed, cards, views)."""
    if session_id is None:
        return None
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return None
    return await auth_service.resolve_session(repo, sid)


def _guest_fingerprint(response: Response, existing: str | None) -> str:
    """Stable per-browser id for guest view dedup (FR-2.3).

    Reuses the id sent by the browser; sets a fresh one only when absent.
    """
    import secrets

    if existing:
        return existing
    guest_id = secrets.token_urlsafe(24)
    response.set_cookie("guest_id", guest_id, max_age=365 * 24 * 3600,
                        httponly=True, samesite="lax", path="/")
    return guest_id


class VideoOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    status: str
    is_private: bool


class SqlVideoRepo:
    """SQLAlchemy implementation of videos_svc.VideoRepo."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, video: Video) -> Video:
        self._session.add(video)
        await self._session.commit()
        await self._session.refresh(video)
        return video

    async def get(self, video_id: uuid.UUID) -> Video | None:
        return await self._session.get(Video, video_id)


async def get_video_repo(
    session: AsyncSession = Depends(get_db_session),
) -> SqlVideoRepo:
    return SqlVideoRepo(session)


def get_storage() -> Storage:
    from app.core.storage import get_storage as _real

    return _real()


def get_queue():
    from app.worker.queue import ArqTranscodeQueue

    return ArqTranscodeQueue()


def get_upload_settings() -> Settings:
    return get_settings()


# --- Stage 2D: viewing endpoints -------------------------------------------
# Repos for interactions are injected as plain callables so tests override
# them the same way as get_video_repo; production wiring returns SQLAlchemy
# implementations (added with the feed/search queries in this stage).


def get_feed_repo():
    from app.api.repos import SqlFeedRepo

    return SqlFeedRepo()


def get_view_repo():
    from app.api.repos import SqlViewRepo

    return SqlViewRepo()


def get_reaction_repo():
    from app.api.repos import SqlReactionRepo

    return SqlReactionRepo()


def get_comment_repo():
    from app.api.repos import SqlCommentRepo

    return SqlCommentRepo()


def get_acl():
    from app.api.repos import SqlAclRepo

    return SqlAclRepo()


class VideoCard(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    status: str
    is_private: bool
    duration_seconds: int
    view_count: int
    play_url: str | None = None
    thumbnail_url: str | None = None


class FeedItem(BaseModel):
    id: uuid.UUID
    title: str
    thumbnail_url: str | None = None
    duration_seconds: int
    view_count: int


class FeedOut(BaseModel):
    items: list[FeedItem]


class ReactionOut(BaseModel):
    likes: int
    dislikes: int


class CommentIn(BaseModel):
    text: str
    parent_id: int | None = None


class CommentOut(BaseModel):
    id: int
    text: str
    parent_id: int | None


class CommentList(BaseModel):
    items: list[CommentOut]


def _thumb_url(video) -> str | None:
    key = getattr(video, "thumbnail_key", None)
    if not key:
        return None
    if getattr(video, "is_private", False):
        return None  # private thumbs are presigned at page render (stage 3 UI)
    return f"/media/{key}"


feed_router = APIRouter(prefix="/api", tags=["feed"])


@feed_router.get("/feed", response_model=FeedOut)
async def feed(
    limit: int = 20,
    offset: int = 0,
    feed_repo=Depends(get_feed_repo),
) -> FeedOut:
    videos = await feed_repo.public_feed(limit=limit, offset=offset)
    return FeedOut(items=[
        FeedItem(
            id=v.id, title=v.title, thumbnail_url=_thumb_url(v),
            duration_seconds=v.duration_seconds, view_count=v.view_count,
        )
        for v in videos
    ])


@feed_router.get("/search", response_model=FeedOut)
async def search(
    q: str = "",
    limit: int = 20,
    offset: int = 0,
    feed_repo=Depends(get_feed_repo),
) -> FeedOut:
    if not q.strip():
        return FeedOut(items=[])
    videos = await feed_repo.search(q.strip(), limit=limit, offset=offset)
    return FeedOut(items=[
        FeedItem(
            id=v.id, title=v.title, thumbnail_url=_thumb_url(v),
            duration_seconds=v.duration_seconds, view_count=v.view_count,
        )
        for v in videos
    ])


@router.get("/{video_id}", response_model=VideoCard)
async def video_card(
    video_id: uuid.UUID,
    user: User | None = Depends(_optional_user),
    repo=Depends(get_video_repo),
    storage: Storage = Depends(get_storage),
    acl=Depends(get_acl),
) -> VideoCard:
    from app.services import access

    video = await repo.get(video_id)
    if video is None:
        raise HTTPException(404, "not found")
    play_url = await access.view_or_none(storage, video, user, acl)
    if play_url is None and not access.can_view(video, user):
        raise HTTPException(404, "not found")  # FR-9: hide existence
    return VideoCard(
        id=video.id, title=video.title, description=video.description,
        status=video.status, is_private=video.is_private,
        duration_seconds=video.duration_seconds, view_count=video.view_count,
        play_url=play_url, thumbnail_url=_thumb_url(video),
    )


@router.post("/{video_id}/view", status_code=http_status.HTTP_204_NO_CONTENT)
async def register_view(
    video_id: uuid.UUID,
    response: Response,
    guest_id: str | None = Cookie(default=None),
    user: User | None = Depends(_optional_user),
    repo=Depends(get_video_repo),
    view_repo=Depends(get_view_repo),
    storage: Storage = Depends(get_storage),
    acl=Depends(get_acl),
) -> None:
    from app.services import access, interactions

    video = await repo.get(video_id)
    if video is None or not await access.can_view_with_acl(video, user, acl):
        raise HTTPException(404, "not found")
    fingerprint = str(user.id) if user is not None else _guest_fingerprint(response, guest_id)
    await interactions.register_view(view_repo, video_id, user.id if user else None, fingerprint)


@router.post("/{video_id}/reactions", response_model=ReactionOut)
async def set_reaction(
    video_id: uuid.UUID,
    payload: dict,
    user: User = Depends(get_current_user),
    repo=Depends(get_video_repo),
    reaction_repo=Depends(get_reaction_repo),
    acl=Depends(get_acl),
) -> ReactionOut:
    from app.services import access, interactions

    video = await repo.get(video_id)
    if video is None or not await access.can_view_with_acl(video, user, acl):
        raise HTTPException(404, "not found")
    try:
        value = int(payload.get("value", 0))
    except (TypeError, ValueError):
        raise HTTPException(422, "value must be 1, -1 or 0") from None
    if value not in (1, -1, 0):
        raise HTTPException(422, "value must be 1, -1 or 0")
    await interactions.set_reaction(reaction_repo, user.id, video_id, value)
    likes, dislikes = await reaction_repo.counts(video_id)
    return ReactionOut(likes=likes, dislikes=dislikes)


@router.post(
    "/{video_id}/comments",
    response_model=CommentOut,
    status_code=http_status.HTTP_201_CREATED,
)
async def add_comment(
    video_id: uuid.UUID,
    payload: CommentIn,
    user: User = Depends(get_current_user),
    repo=Depends(get_video_repo),
    comment_repo=Depends(get_comment_repo),
    acl=Depends(get_acl),
) -> CommentOut:
    from app.services import access, interactions

    video = await repo.get(video_id)
    if video is None or not await access.can_view_with_acl(video, user, acl):
        raise HTTPException(404, "not found")
    try:
        comment = await interactions.add_comment(
            comment_repo, video_id, user.id, payload.text, payload.parent_id
        )
    except interactions.InvalidComment:
        raise HTTPException(422, "comment text must be 1..2000 chars") from None
    return CommentOut(id=comment.id, text=comment.text, parent_id=comment.parent_id)


@router.get("/{video_id}/comments", response_model=CommentList)
async def list_comments(
    video_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    repo=Depends(get_video_repo),
    comment_repo=Depends(get_comment_repo),
    acl=Depends(get_acl),
    user: User | None = Depends(_optional_user),
) -> CommentList:
    from app.services import access

    video = await repo.get(video_id)
    if video is None or not await access.can_view_with_acl(video, user, acl):
        raise HTTPException(404, "not found")
    comments = await comment_repo.list_for_video(video_id, limit=limit, offset=offset)
    return CommentList(items=[
        CommentOut(id=c.id, text=c.text, parent_id=c.parent_id) for c in comments
    ])


@router.post("", response_model=VideoOut, status_code=http_status.HTTP_202_ACCEPTED)
async def upload_video(
    file: UploadFile = File(...),
    title: str = Form(""),
    description: str = Form(""),
    user: User = Depends(get_current_user),
    repo: SqlVideoRepo = Depends(get_video_repo),
    storage: Storage = Depends(get_storage),
    queue=Depends(get_queue),
    settings: Settings = Depends(get_upload_settings),
) -> VideoOut:
    if not title.strip():
        raise HTTPException(422, "title is required")

    payload = await file.read()
    args = videos_svc.UploadArgs(
        filename=file.filename or "",
        data=_Seekable(payload),
        declared_size=len(payload),
        max_size=settings.max_upload_size,
    )
    try:
        ext = videos_svc.validate_upload(args)
    except videos_svc.UploadRejected as exc:
        reason = str(exc)
        if "size" in reason:
            raise HTTPException(413, reason) from None
        raise HTTPException(415, reason) from None

    video_id = uuid.uuid4()
    raw_key = videos_svc.build_raw_key(video_id, ext)
    storage.put(BUCKETS["raw"], raw_key, payload)

    video = videos_svc.new_video(
        user_id=user.id,
        title=title.strip(),
        description=description.strip(),
        raw_key=raw_key,
        size_bytes=len(payload),
    )
    video.id = video_id
    created = await repo.create(video)
    await queue.enqueue_transcode(created.id)
    return VideoOut(
        id=created.id,
        title=created.title,
        description=created.description,
        status=created.status,
        is_private=created.is_private,
    )


class _Seekable:
    """Adapter: bytes → a seekable binary file object for UploadArgs."""

    def __init__(self, data: bytes) -> None:
        self._buf = __import__("io").BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def seek(self, pos: int) -> None:
        self._buf.seek(pos)
