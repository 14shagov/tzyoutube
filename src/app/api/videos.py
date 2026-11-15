"""HTTP layer for videos: upload endpoint (stage 2B).

Auth via get_current_user; storage/queue/repo injected as dependencies
so tests can override them with fakes.
"""

import asyncio
import re
import secrets
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi import status as http_status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
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


# Guest fingerprint charset: the DB column is String(64); anything outside
# this set is rejected and replaced by a fresh server-issued token.
_GUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _guest_fingerprint(response: Response, existing: str | None) -> str:
    """Stable per-browser id for guest view dedup (FR-2.3).

    Reuses the id sent by the browser ONLY if it matches the strict charset;
    otherwise (or when absent) issues a fresh token — a malformed/oversized
    cookie must never reach the DB as a fingerprint.
    """
    if existing and _GUEST_ID_RE.match(existing):
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

    async def update(self, video: Video) -> Video:
        self._session.add(video)
        await self._session.commit()
        await self._session.refresh(video)
        return video

    async def list_user_videos(self, user_id: uuid.UUID, *, limit: int, offset: int):
        """Owner's own videos incl. private/failed — deleted ones are gone (FR-10)."""
        stmt = (
            select(Video)
            .where(Video.user_id == user_id, Video.status != "deleted")
            .order_by(Video.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


async def get_video_repo(
    session: AsyncSession = Depends(get_db_session),
) -> SqlVideoRepo:
    return SqlVideoRepo(session)


def get_storage() -> Storage:
    from app.core.storage import get_storage as _real

    return _real()


def get_queue(request: Request):
    """FastAPI dependency: the process-wide queue from app.state (C1).

    Previously a new ArqTranscodeQueue (own lazy arq pool) was built per
    request, leaking a Redis pool per upload.
    """
    return request.app.state.queue


def get_upload_settings() -> Settings:
    return get_settings()


# --- Stage 2D: viewing endpoints -------------------------------------------
# Repos for interactions are injected as plain callables so tests override
# them the same way as get_video_repo; production wiring returns SQLAlchemy
# implementations (added with the feed/search queries in this stage).


def get_feed_repo(session: AsyncSession = Depends(get_db_session)):
    from app.api.repos import SqlFeedRepo

    return SqlFeedRepo(session)


def get_view_repo(session: AsyncSession = Depends(get_db_session)):
    from app.api.repos import SqlViewRepo

    return SqlViewRepo(session)


def get_reaction_repo(session: AsyncSession = Depends(get_db_session)):
    from app.api.repos import SqlReactionRepo

    return SqlReactionRepo(session)


def get_comment_repo(session: AsyncSession = Depends(get_db_session)):
    from app.api.repos import SqlCommentRepo

    return SqlCommentRepo(session)


def get_acl(session: AsyncSession = Depends(get_db_session)):
    from app.api.repos import SqlAclRepo

    return SqlAclRepo(session)


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
    parent_id: uuid.UUID | None = None


class CommentOut(BaseModel):
    id: uuid.UUID
    text: str
    parent_id: uuid.UUID | None


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
    limit: Annotated[int, Query(ge=0, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
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
    limit: Annotated[int, Query(ge=0, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
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


class MyVideoItem(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    is_private: bool
    failure_reason: str | None = None
    view_count: int
    duration_seconds: int
    created_at: datetime


class MyVideosOut(BaseModel):
    items: list[MyVideoItem]


@feed_router.get("/me/videos", response_model=MyVideosOut)
async def my_videos(
    limit: Annotated[int, Query(ge=0, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    user: User = Depends(get_current_user),
    repo: SqlVideoRepo = Depends(get_video_repo),
) -> MyVideosOut:
    """FR-9/FR-10: my videos incl. private, with processing status + failure_reason."""
    videos = await repo.list_user_videos(user.id, limit=limit, offset=offset)
    return MyVideosOut(items=[
        MyVideoItem(
            id=v.id, title=v.title, status=v.status, is_private=v.is_private,
            failure_reason=v.failure_reason, view_count=v.view_count,
            duration_seconds=v.duration_seconds, created_at=v.created_at,
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
    guest_id: Annotated[str | None, Cookie(max_length=64)] = None,
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
    # Contract: the service takes the user OBJECT (None for guests) and
    # reads user.id itself — passing user.id here would make it user.id.id.
    await interactions.register_view(view_repo, video_id, user, fingerprint)


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
    except ValueError:
        # SqlCommentRepo rejects a missing or nested parent with ValueError.
        raise HTTPException(422, "invalid parent_id") from None
    return CommentOut(id=comment.id, text=comment.text, parent_id=comment.parent_id)


@router.get("/{video_id}/comments", response_model=CommentList)
async def list_comments(
    video_id: uuid.UUID,
    limit: Annotated[int, Query(ge=0, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
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
    request: Request,
    file: UploadFile = File(...),
    # Video.title is String(200): bound at validation so an over-length
    # title is a 422, not a DB error (500).
    title: Annotated[str, Form(max_length=200)] = "",
    description: str = Form(""),
    user: User = Depends(get_current_user),
    repo: SqlVideoRepo = Depends(get_video_repo),
    storage: Storage = Depends(get_storage),
    queue=Depends(get_queue),
    settings: Settings = Depends(get_upload_settings),
) -> VideoOut:
    if not title.strip():
        raise HTTPException(422, "title is required")

    max_size = settings.max_upload_size

    # C5: Content-Length pre-check — reject an obviously oversized upload
    # with 413 before reading a single byte of the body (nginx allows 512m
    # only on this location, but the app must not rely on the proxy).
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            raise HTTPException(400, "invalid Content-Length") from None
        if declared > max_size:
            raise HTTPException(413, "size over limit") from None

    # Stream the upload to a temp file, enforcing the size limit while
    # reading — the file never sits whole in memory (m02086 task 2).
    chunk_limit = 1024 * 1024  # 1 MiB chunks
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
        total = 0
        while True:
            chunk = await file.read(chunk_limit)
            if not chunk:
                break
            total += len(chunk)
            if total > max_size:
                tmp.close()
                Path(tmp_path).unlink(missing_ok=True)
                raise HTTPException(413, "size over limit")
            tmp.write(chunk)

    try:
        head = await asyncio.to_thread(_read_head, tmp_path)
        args = videos_svc.UploadArgs(
            filename=file.filename or "",
            data=_Seekable(head),
            declared_size=total,
            max_size=max_size,
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
        # minio-py is blocking: run the upload in a worker thread.
        obj = await asyncio.to_thread(
            storage.put_stream, BUCKETS["raw"], raw_key, tmp_path,
            videos_svc.content_type_for_ext(ext),
        )

        video = videos_svc.new_video(
            user_id=user.id,
            title=title.strip(),
            description=description.strip(),
            raw_key=raw_key,
            size_bytes=obj.size,
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
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# --- Stage 3C: video management + ACL whitelist (FR-9, FR-10) ----------------


class VideoPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    is_private: bool | None = None


class VideoManageOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    status: str
    is_private: bool
    failure_reason: str | None = None


class AclAddIn(BaseModel):
    email: EmailStr


class AclEntryOut(BaseModel):
    user_id: uuid.UUID
    email: str
    channel_name: str


class AclListOut(BaseModel):
    items: list[AclEntryOut]


async def _owned_video(video_id: uuid.UUID, user: User, repo: SqlVideoRepo) -> Video:
    video = await repo.get(video_id)
    if video is None or video.status == "deleted":
        raise HTTPException(404, "not found")
    if video.user_id != user.id:
        raise HTTPException(403, "owner only")
    return video


@router.patch("/{video_id}", response_model=VideoManageOut)
async def patch_video(
    video_id: uuid.UUID,
    payload: VideoPatchIn,
    user: User = Depends(get_current_user),
    repo: SqlVideoRepo = Depends(get_video_repo),
) -> VideoManageOut:
    video = await _owned_video(video_id, user, repo)
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(422, "title must not be blank")
        video.title = title
    if payload.description is not None:
        video.description = payload.description
    if payload.is_private is not None:
        video.is_private = payload.is_private
    video = await repo.update(video)
    return VideoManageOut(
        id=video.id, title=video.title, description=video.description,
        status=video.status, is_private=video.is_private,
        failure_reason=video.failure_reason,
    )


@router.delete("/{video_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_video(
    video_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repo: SqlVideoRepo = Depends(get_video_repo),
    queue=Depends(get_queue),
) -> None:
    """FR-10: soft delete — the row is marked deleted and the worker job
    removes the S3 objects and then the row itself."""
    from app.services import access

    video = await repo.get(video_id)
    if video is None or video.status == "deleted":
        raise HTTPException(404, "not found")
    if video.user_id != user.id and user.role not in access.STAFF_ROLES:
        raise HTTPException(403, "owner or staff only")
    video.status = "deleted"
    await repo.update(video)
    await queue.enqueue_delete(video.id)


@router.get("/{video_id}/acl", response_model=AclListOut)
async def list_acl(
    video_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repo: SqlVideoRepo = Depends(get_video_repo),
    acl=Depends(get_acl),
) -> AclListOut:
    await _owned_video(video_id, user, repo)
    entries = await acl.list_entries(video_id)
    return AclListOut(items=[
        AclEntryOut(user_id=u.id, email=u.email, channel_name=u.channel_name)
        for u in entries
    ])


@router.post("/{video_id}/acl", status_code=http_status.HTTP_204_NO_CONTENT)
async def add_acl(
    video_id: uuid.UUID,
    payload: AclAddIn,
    user: User = Depends(get_current_user),
    repo: SqlVideoRepo = Depends(get_video_repo),
    acl=Depends(get_acl),
    user_repo=Depends(get_user_repo),
) -> None:
    """Grant a user access to a private video, by email; idempotent (FR-9)."""
    await _owned_video(video_id, user, repo)
    target = await user_repo.get_by_email(payload.email)
    if target is None:
        raise HTTPException(404, "user not found")
    if target.id == user.id:
        raise HTTPException(422, "cannot add yourself to the ACL")
    await acl.add(video_id, target.id)


@router.delete("/{video_id}/acl/{user_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def remove_acl(
    video_id: uuid.UUID,
    user_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repo: SqlVideoRepo = Depends(get_video_repo),
    acl=Depends(get_acl),
) -> None:
    await _owned_video(video_id, user, repo)
    await acl.remove(video_id, user_id)


class _Seekable:
    """Adapter: bytes → a seekable binary file object for UploadArgs."""

    def __init__(self, data: bytes) -> None:
        self._buf = __import__("io").BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def seek(self, pos: int) -> None:
        self._buf.seek(pos)


def _read_head(path: str) -> bytes:
    """Blocking helper: first bytes for magic-byte sniffing."""
    with open(path, "rb") as fh:
        return fh.read(32)
