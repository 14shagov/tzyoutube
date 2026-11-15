"""Channel profile and subscriptions (FR-3, stage 3A/3B).

A channel IS the user row (channel_id == users.id). Repos are SQLAlchemy
implementations in app.api.repos; these protocols + helpers define the
contract the HTTP layer and tests use.
"""

import uuid
from typing import Protocol

from app.models import User, Video


class SelfSubscription(Exception):
    """Subscribing to your own channel is rejected (422 at the API)."""


class ChannelRepo(Protocol):
    async def get_user(self, user_id: uuid.UUID) -> User | None: ...
    async def subscriber_count(self, channel_id: uuid.UUID) -> int: ...
    async def public_video_count(self, channel_id: uuid.UUID) -> int: ...
    async def list_channel_videos(
        self, channel_id: uuid.UUID, *, viewer_id: uuid.UUID | None, limit: int, offset: int
    ) -> list[Video]: ...
    async def channel_name_taken(self, name: str, *, exclude_user_id: uuid.UUID) -> bool: ...
    async def update_channel(
        self, user: User, *, channel_name: str | None, description: str | None
    ) -> User: ...


class SubscriptionRepo(Protocol):
    async def is_subscribed(self, subscriber_id: uuid.UUID, channel_id: uuid.UUID) -> bool: ...
    async def subscribe(self, subscriber_id: uuid.UUID, channel_id: uuid.UUID) -> None: ...
    async def unsubscribe(self, subscriber_id: uuid.UUID, channel_id: uuid.UUID) -> None: ...
    async def subscribed_channels(
        self, subscriber_id: uuid.UUID, *, limit: int, offset: int
    ) -> list[User]: ...
    async def subscriptions_feed(
        self, subscriber_id: uuid.UUID, *, limit: int, offset: int
    ) -> list[Video]: ...


async def subscribe(repo: SubscriptionRepo, user: User, channel_id: uuid.UUID) -> None:
    """Idempotent subscribe; self-subscription is a client error."""
    if user.id == channel_id:
        raise SelfSubscription("cannot subscribe to your own channel")
    await repo.subscribe(user.id, channel_id)
