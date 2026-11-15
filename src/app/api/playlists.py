"""HTTP layer for playlists (FR-3, stage 3D).

Visibility: public playlists are readable by anyone, but the item list is
filtered to videos the viewer may see (access.can_view). Private playlists
are owner-only — everyone else gets 404 (existence hidden).
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.videos import _optional_user, get_acl, get_video_repo
from app.core.db import get_db_session
from app.models import Playlist, User
from app.services import access
from app.services import playlists as playlists_svc

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


def get_playlist_repo(session: AsyncSession = Depends(get_db_session)):
    from app.api.repos import SqlPlaylistRepo

    return SqlPlaylistRepo(session)


class PlaylistIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    is_private: bool = False


class PlaylistPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    is_private: bool | None = None


class PlaylistItemOut(BaseModel):
    video_id: uuid.UUID
    title: str
    position: int


class PlaylistOut(BaseModel):
    id: uuid.UUID
    title: str
    is_private: bool
    created_at: datetime
    item_count: int = 0


class PlaylistDetailOut(PlaylistOut):
    items: list[PlaylistItemOut] = []


class PlaylistListOut(BaseModel):
    items: list[PlaylistOut]


class ItemAddIn(BaseModel):
    video_id: uuid.UUID
    position: int | None = Field(default=None, ge=0)


def _out(playlist: Playlist, item_count: int = 0) -> PlaylistOut:
    return PlaylistOut(
        id=playlist.id, title=playlist.title, is_private=playlist.is_private,
        created_at=playlist.created_at, item_count=item_count,
    )


async def _owned_playlist(playlist_id: uuid.UUID, user: User, repo) -> Playlist:
    playlist = await repo.get(playlist_id)
    if playlist is None:
        raise HTTPException(404, "not found")
    if playlist.user_id != user.id:
        raise HTTPException(403, "owner only")
    return playlist


@router.post("", response_model=PlaylistOut, status_code=http_status.HTTP_201_CREATED)
async def create_playlist(
    payload: PlaylistIn,
    user: User = Depends(get_current_user),
    repo=Depends(get_playlist_repo),
) -> PlaylistOut:
    title = payload.title.strip()
    if not title:
        raise HTTPException(422, "title must not be blank")
    playlist = await repo.create(
        playlists_svc.new_playlist(user.id, title, payload.is_private)
    )
    return _out(playlist)


@router.get("/{playlist_id}", response_model=PlaylistDetailOut)
async def get_playlist(
    playlist_id: uuid.UUID,
    user: User | None = Depends(_optional_user),
    repo=Depends(get_playlist_repo),
) -> PlaylistDetailOut:
    playlist = await repo.get(playlist_id)
    if playlist is None or not playlists_svc.can_view_playlist(playlist, user):
        raise HTTPException(404, "not found")
    pairs = await repo.list_items_with_videos(playlist_id)
    # Only videos this viewer may actually watch are listed.
    items = [
        PlaylistItemOut(video_id=item.video_id, title=video.title, position=item.position)
        for item, video in pairs
        if access.can_view(video, user)
    ]
    return PlaylistDetailOut(**_out(playlist, item_count=len(items)).model_dump(), items=items)


@router.patch("/{playlist_id}", response_model=PlaylistOut)
async def patch_playlist(
    playlist_id: uuid.UUID,
    payload: PlaylistPatchIn,
    user: User = Depends(get_current_user),
    repo=Depends(get_playlist_repo),
) -> PlaylistOut:
    playlist = await _owned_playlist(playlist_id, user, repo)
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(422, "title must not be blank")
        playlist.title = title
    if payload.is_private is not None:
        playlist.is_private = payload.is_private
    playlist = await repo.update(playlist)
    return _out(playlist, item_count=await repo.item_count(playlist.id))


@router.delete("/{playlist_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_playlist(
    playlist_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repo=Depends(get_playlist_repo),
) -> None:
    await _owned_playlist(playlist_id, user, repo)
    await repo.delete(playlist_id)


@router.post(
    "/{playlist_id}/items",
    response_model=PlaylistItemOut,
    status_code=http_status.HTTP_201_CREATED,
)
async def add_item(
    playlist_id: uuid.UUID,
    payload: ItemAddIn,
    user: User = Depends(get_current_user),
    repo=Depends(get_playlist_repo),
    video_repo=Depends(get_video_repo),
    acl=Depends(get_acl),
) -> PlaylistItemOut:
    await _owned_playlist(playlist_id, user, repo)
    video = await video_repo.get(payload.video_id)
    # Unknown, deleted or not-viewable-by-owner videos all answer 404.
    if (
        video is None
        or video.status == "deleted"
        or not await access.can_view_with_acl(video, user, acl)
    ):
        raise HTTPException(404, "not found")
    if payload.position is not None:
        position = payload.position
    else:
        position = await repo.max_position(playlist_id) + 1
    try:
        item = await repo.add_item(playlist_id, video.id, position)
    except playlists_svc.DuplicateItem:
        raise HTTPException(409, "video already in playlist") from None
    return PlaylistItemOut(video_id=item.video_id, title=video.title, position=item.position)


@router.delete(
    "/{playlist_id}/items/{video_id}", status_code=http_status.HTTP_204_NO_CONTENT
)
async def remove_item(
    playlist_id: uuid.UUID,
    video_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repo=Depends(get_playlist_repo),
) -> None:
    await _owned_playlist(playlist_id, user, repo)
    await repo.remove_item(playlist_id, video_id)
