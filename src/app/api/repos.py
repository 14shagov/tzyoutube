"""SQLAlchemy repositories for feed / views / reactions / comments / ACL.

Used by the stage 2D viewing endpoints; tests inject fakes instead.
The interaction *services* (app/services/interactions.py) define the
protocols these repositories implement.
"""

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Comment, Reaction, Subscription, User, Video, VideoAcl, VideoView


def escape_ilike(value: str, escape: str = "\\") -> str:
    """Escape LIKE wildcards (and the escape char itself) in a user query.

    Backslash first, then % and _, so a crafted input like "\\%" cannot
    smuggle an unescaped wildcard through.
    """
    return (
        value.replace(escape, escape + escape)
        .replace("%", escape + "%")
        .replace("_", escape + "_")
    )


class SqlFeedRepo:
    """Public feed and title search (FR-1, FR-7)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def public_feed(self, limit: int = 20, offset: int = 0):
        stmt = (
            select(Video)
            .where(Video.status == "ready", Video.is_private.is_(False))
            .order_by(Video.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def search(self, query: str, limit: int = 20, offset: int = 0):
        stmt = (
            select(Video)
            .where(
                Video.status == "ready",
                Video.is_private.is_(False),
                # User input goes through escape_ilike: a raw % or _ in the
                # query would match every row (B6).
                Video.title.ilike(f"%{escape_ilike(query)}%", escape="\\"),
            )
            .order_by(Video.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class SqlViewRepo:
    """View dedup and view_count increment (FR-2.3)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def recent_view(self, video_id: uuid.UUID, fingerprint: str, since):
        stmt = select(VideoView.id).where(
            VideoView.video_id == video_id,
            VideoView.viewer_fingerprint == fingerprint,
            VideoView.created_at >= since,
        )
        result = await self.session.execute(stmt)
        return result.first() is not None

    async def add_view(self, video_id, user_id, fingerprint):
        self.session.add(
            VideoView(video_id=video_id, user_id=user_id, viewer_fingerprint=fingerprint)
        )
        await self.session.commit()

    async def increment_view_count(self, video_id):
        await self.session.execute(
            Video.__table__.update()
            .where(Video.id == video_id)
            .values(view_count=Video.view_count + 1)
        )
        await self.session.commit()


class SqlReactionRepo:
    """Likes/dislikes per user (FR-4)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _get_obj(self, user_id, video_id) -> Reaction | None:
        stmt = select(Reaction).where(
            Reaction.user_id == user_id, Reaction.video_id == video_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get(self, user_id, video_id) -> int | None:
        """Protocol contract: the reaction VALUE (1/-1) or None — not the row."""
        row = await self._get_obj(user_id, video_id)
        return row.value if row is not None else None

    async def upsert(self, user_id, video_id, value):
        existing = await self._get_obj(user_id, video_id)
        if existing:
            existing.value = value
        else:
            self.session.add(Reaction(user_id=user_id, video_id=video_id, value=value))
        await self.session.commit()

    async def delete(self, user_id, video_id):
        await self.session.execute(
            delete(Reaction).where(
                Reaction.user_id == user_id, Reaction.video_id == video_id
            )
        )
        await self.session.commit()

    async def counts(self, video_id):
        stmt = (
            select(Reaction.value, func.count())
            .where(Reaction.video_id == video_id)
            .group_by(Reaction.value)
        )
        result = await self.session.execute(stmt)
        counts = dict(result.all())
        return int(counts.get(1, 0)), int(counts.get(-1, 0))


class SqlCommentRepo:
    """One-level comments (FR-3)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, video_id, user_id, text, parent_id=None):
        if parent_id is not None:
            # enforce single nesting level: parent itself must be a root
            parent = await self.session.get(Comment, parent_id)
            if parent is None or parent.parent_id is not None:
                raise ValueError("invalid parent_id")
        comment = Comment(
            video_id=video_id, user_id=user_id, text=text, parent_id=parent_id
        )
        self.session.add(comment)
        await self.session.commit()
        await self.session.refresh(comment)
        return comment

    async def list_for_video(self, video_id, limit=50, offset=0):
        stmt = (
            select(Comment)
            .where(Comment.video_id == video_id)
            .order_by(Comment.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class SqlChannelRepo:
    """Channel profile + channel video listing (FR-3, stage 3A).

    Visibility rule: the owner sees every non-deleted video of the channel
    (including private/hidden/processing — with the status in the response);
    everyone else sees only ready+public ones.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_user(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def subscriber_count(self, channel_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(Subscription).where(
            Subscription.channel_id == channel_id
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def public_video_count(self, channel_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(Video).where(
            Video.user_id == channel_id,
            Video.status == "ready",
            Video.is_private.is_(False),
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_channel_videos(
        self, channel_id: uuid.UUID, *, viewer_id: uuid.UUID | None, limit: int, offset: int
    ):
        stmt = select(Video).where(Video.user_id == channel_id)
        if viewer_id == channel_id:
            stmt = stmt.where(Video.status != "deleted")
        else:
            # Hidden/failed/processing/private stay invisible to non-owners (3E).
            stmt = stmt.where(Video.status == "ready", Video.is_private.is_(False))
        stmt = stmt.order_by(Video.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def channel_name_taken(self, name: str, *, exclude_user_id: uuid.UUID) -> bool:
        stmt = select(func.count()).select_from(User).where(
            User.channel_name == name, User.id != exclude_user_id
        )
        return (await self.session.execute(stmt)).scalar_one() > 0

    async def update_channel(
        self, user: User, *, channel_name: str | None, description: str | None
    ) -> User:
        if channel_name is not None:
            user.channel_name = channel_name
        if description is not None:
            user.channel_description = description
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user


class SqlSubscriptionRepo:
    """Subscriptions (FR-3): idempotent subscribe/unsubscribe, my channels, sub feed."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_subscribed(self, subscriber_id: uuid.UUID, channel_id: uuid.UUID) -> bool:
        stmt = select(func.count()).select_from(Subscription).where(
            Subscription.subscriber_id == subscriber_id,
            Subscription.channel_id == channel_id,
        )
        return (await self.session.execute(stmt)).scalar_one() > 0

    async def subscribe(self, subscriber_id: uuid.UUID, channel_id: uuid.UUID) -> None:
        if await self.is_subscribed(subscriber_id, channel_id):
            return  # idempotent
        self.session.add(Subscription(subscriber_id=subscriber_id, channel_id=channel_id))
        await self.session.commit()

    async def unsubscribe(self, subscriber_id: uuid.UUID, channel_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(Subscription).where(
                Subscription.subscriber_id == subscriber_id,
                Subscription.channel_id == channel_id,
            )
        )
        await self.session.commit()

    async def subscribed_channels(
        self, subscriber_id: uuid.UUID, *, limit: int, offset: int
    ):
        stmt = (
            select(User)
            .join(Subscription, Subscription.channel_id == User.id)
            .where(Subscription.subscriber_id == subscriber_id)
            .order_by(Subscription.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def subscriptions_feed(
        self, subscriber_id: uuid.UUID, *, limit: int, offset: int
    ):
        stmt = (
            select(Video)
            .join(Subscription, Subscription.channel_id == Video.user_id)
            .where(
                Subscription.subscriber_id == subscriber_id,
                Video.status == "ready",
                Video.is_private.is_(False),
            )
            .order_by(Video.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class SqlAclRepo:
    """video_acl whitelist lookups (FR-8)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_allowed(self, video_id, user_id) -> bool:
        if user_id is None:
            return False
        stmt = select(func.count()).select_from(VideoAcl).where(
            VideoAcl.video_id == video_id, VideoAcl.user_id == user_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one() > 0

    async def add(self, video_id, user_id) -> None:
        """Idempotent whitelist grant (FR-9)."""
        if not await self.is_allowed(video_id, user_id):
            self.session.add(VideoAcl(video_id=video_id, user_id=user_id))
            await self.session.commit()

    async def remove(self, video_id, user_id) -> None:
        """Idempotent whitelist revoke (FR-9)."""
        await self.session.execute(
            delete(VideoAcl).where(
                VideoAcl.video_id == video_id, VideoAcl.user_id == user_id
            )
        )
        await self.session.commit()

    async def list_entries(self, video_id):
        """Whitelisted users of a video, for the owner's ACL management UI."""
        stmt = (
            select(User)
            .join(VideoAcl, VideoAcl.user_id == User.id)
            .where(VideoAcl.video_id == video_id)
            .order_by(User.channel_name)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())