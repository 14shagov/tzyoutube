"""HTTP layer for channels (stage 3A/3B, FR-3).

A channel is the user's public profile: GET /api/channels/{id}, its videos,
PATCH by the owner. Providers reuse the same function objects as
app.api.videos / app.api.auth so tests override them identically.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.videos import FeedItem, FeedOut, _optional_user, _thumb_url
from app.core.db import get_db_session
from app.models import User
from app.services import social
from app.services.social import ChannelRepo, SubscriptionRepo

router = APIRouter(prefix="/api/channels", tags=["channels"])
# /api/subscriptions and /api/feed/subscriptions live under /api (3B).
api_router = APIRouter(prefix="/api", tags=["subscriptions"])

# Avatar stub until real avatar uploads exist (FR-3 placeholder).
DEFAULT_AVATAR_URL = "/static/avatar.png"


def get_channel_repo(session: AsyncSession = Depends(get_db_session)) -> ChannelRepo:
    from app.api.repos import SqlChannelRepo

    return SqlChannelRepo(session)


def get_subscription_repo(
    session: AsyncSession = Depends(get_db_session),
) -> SubscriptionRepo:
    from app.api.repos import SqlSubscriptionRepo

    return SqlSubscriptionRepo(session)


class ChannelOut(BaseModel):
    id: uuid.UUID
    channel_name: str
    description: str
    avatar_url: str
    subscriber_count: int
    public_video_count: int
    subscribed: bool


class ChannelVideoItem(BaseModel):
    id: uuid.UUID
    title: str
    thumbnail_url: str | None = None
    duration_seconds: int
    view_count: int
    status: str
    is_private: bool


class ChannelVideosOut(BaseModel):
    items: list[ChannelVideoItem]


class ChannelPatchIn(BaseModel):
    channel_name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=5000)


class ChannelSummary(BaseModel):
    id: uuid.UUID
    channel_name: str
    avatar_url: str


class SubscriptionListOut(BaseModel):
    items: list[ChannelSummary]


def _channel_out(user: User, sub_count: int, vid_count: int, subscribed: bool) -> ChannelOut:
    return ChannelOut(
        id=user.id,
        channel_name=user.channel_name,
        # default="" applies at flush; an unflushed instance can still be None.
        description=user.channel_description or "",
        avatar_url=DEFAULT_AVATAR_URL,
        subscriber_count=sub_count,
        public_video_count=vid_count,
        subscribed=subscribed,
    )


@router.get("/{channel_id}", response_model=ChannelOut)
async def get_channel(
    channel_id: uuid.UUID,
    viewer: User | None = Depends(_optional_user),
    channel_repo: ChannelRepo = Depends(get_channel_repo),
    sub_repo: SubscriptionRepo = Depends(get_subscription_repo),
) -> ChannelOut:
    user = await channel_repo.get_user(channel_id)
    if user is None:
        raise HTTPException(404, "channel not found")
    subscribed = (
        viewer is not None and await sub_repo.is_subscribed(viewer.id, channel_id)
    )
    return _channel_out(
        user,
        await channel_repo.subscriber_count(channel_id),
        await channel_repo.public_video_count(channel_id),
        subscribed,
    )


@router.get("/{channel_id}/videos", response_model=ChannelVideosOut)
async def channel_videos(
    channel_id: uuid.UUID,
    limit: Annotated[int, Query(ge=0, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    viewer: User | None = Depends(_optional_user),
    channel_repo: ChannelRepo = Depends(get_channel_repo),
) -> ChannelVideosOut:
    if await channel_repo.get_user(channel_id) is None:
        raise HTTPException(404, "channel not found")
    videos = await channel_repo.list_channel_videos(
        channel_id, viewer_id=viewer.id if viewer else None, limit=limit, offset=offset
    )
    return ChannelVideosOut(items=[
        ChannelVideoItem(
            id=v.id, title=v.title, thumbnail_url=_thumb_url(v),
            duration_seconds=v.duration_seconds, view_count=v.view_count,
            status=v.status, is_private=v.is_private,
        )
        for v in videos
    ])


@router.patch("/{channel_id}", response_model=ChannelOut)
async def patch_channel(
    channel_id: uuid.UUID,
    payload: ChannelPatchIn,
    user: User = Depends(get_current_user),
    channel_repo: ChannelRepo = Depends(get_channel_repo),
    sub_repo: SubscriptionRepo = Depends(get_subscription_repo),
) -> ChannelOut:
    channel = await channel_repo.get_user(channel_id)
    if channel is None:
        raise HTTPException(404, "channel not found")
    if user.id != channel_id:
        raise HTTPException(403, "only the owner can edit the channel")
    if payload.channel_name is not None and await channel_repo.channel_name_taken(
        payload.channel_name, exclude_user_id=user.id
    ):
        raise HTTPException(409, "channel name already taken")
    updated = await channel_repo.update_channel(
        channel,
        channel_name=payload.channel_name,
        description=payload.description,
    )
    return _channel_out(
        updated,
        await channel_repo.subscriber_count(channel_id),
        await channel_repo.public_video_count(channel_id),
        # Self-subscription is forbidden, so the owner's own flag is False.
        await sub_repo.is_subscribed(user.id, channel_id),
    )


# --- 3B: subscriptions ------------------------------------------------------


@router.post("/{channel_id}/subscribe", status_code=http_status.HTTP_204_NO_CONTENT)
async def subscribe(
    channel_id: uuid.UUID,
    user: User = Depends(get_current_user),
    channel_repo: ChannelRepo = Depends(get_channel_repo),
    sub_repo: SubscriptionRepo = Depends(get_subscription_repo),
) -> None:
    if await channel_repo.get_user(channel_id) is None:
        raise HTTPException(404, "channel not found")
    try:
        await social.subscribe(sub_repo, user, channel_id)
    except social.SelfSubscription:
        raise HTTPException(422, "cannot subscribe to your own channel") from None


@router.delete("/{channel_id}/subscribe", status_code=http_status.HTTP_204_NO_CONTENT)
async def unsubscribe(
    channel_id: uuid.UUID,
    user: User = Depends(get_current_user),
    channel_repo: ChannelRepo = Depends(get_channel_repo),
    sub_repo: SubscriptionRepo = Depends(get_subscription_repo),
) -> None:
    if await channel_repo.get_user(channel_id) is None:
        raise HTTPException(404, "channel not found")
    await sub_repo.unsubscribe(user.id, channel_id)  # idempotent


@api_router.get("/subscriptions", response_model=SubscriptionListOut)
async def my_subscriptions(
    limit: Annotated[int, Query(ge=0, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    user: User = Depends(get_current_user),
    sub_repo: SubscriptionRepo = Depends(get_subscription_repo),
) -> SubscriptionListOut:
    channels = await sub_repo.subscribed_channels(user.id, limit=limit, offset=offset)
    return SubscriptionListOut(items=[
        ChannelSummary(id=c.id, channel_name=c.channel_name, avatar_url=DEFAULT_AVATAR_URL)
        for c in channels
    ])


@api_router.get("/feed/subscriptions", response_model=FeedOut)
async def subscriptions_feed(
    limit: Annotated[int, Query(ge=0, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    user: User = Depends(get_current_user),
    sub_repo: SubscriptionRepo = Depends(get_subscription_repo),
) -> FeedOut:
    videos = await sub_repo.subscriptions_feed(user.id, limit=limit, offset=offset)
    return FeedOut(items=[
        FeedItem(
            id=v.id, title=v.title, thumbnail_url=_thumb_url(v),
            duration_seconds=v.duration_seconds, view_count=v.view_count,
        )
        for v in videos
    ])
