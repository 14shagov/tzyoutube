"""HTTP layer for videos: upload endpoint (stage 2B).

Auth via get_current_user; storage/queue/repo injected as dependencies
so tests can override them with fakes.
"""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi import status as http_status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.config import Settings, get_settings
from app.core.db import get_db_session
from app.core.storage import BUCKETS, Storage
from app.models import User, Video
from app.services import videos as videos_svc

router = APIRouter(prefix="/api/videos", tags=["videos"])


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
