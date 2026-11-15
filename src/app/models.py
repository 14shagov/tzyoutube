"""SQLAlchemy declarative base and ORM models. Media files live in S3 (Silo);
the DB keeps only object keys (see docs/db-schema.md)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    # 3A: channel profile description (FR-3), empty by default.
    channel_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("role IN ('user','moderator','admin')", name="ck_users_role"),
    )

    videos: Mapped[list["Video"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class SessionToken(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="processing")
    raw_key: Mapped[str] = mapped_column(String(200), nullable=False)
    video_key: Mapped[str | None] = mapped_column(String(200))
    thumbnail_key: Mapped[str | None] = mapped_column(String(200))
    # C2: why the transcode failed (ffmpeg output tail / timeout / probe).
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    view_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('processing','ready','failed','hidden','deleted')",
            name="ck_videos_status",
        ),
        Index("ix_videos_status_created", "status", "created_at"),
        Index("ix_videos_user_created", "user_id", "created_at"),
        Index(
            "ix_videos_public_feed",
            "created_at",
            postgresql_where=text("status = 'ready' AND NOT is_private"),
        ),
    )

    owner: Mapped[User] = relationship(back_populates="videos")


class VideoView(Base):
    __tablename__ = "video_views"

    id: Mapped[uuid.UUID] = _uuid_pk()
    video_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    viewer_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Reaction(Base):
    __tablename__ = "reactions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("value IN (1, -1)", name="ck_reactions_value"),
        Index("uq_reactions_user_video", "user_id", "video_id", unique=True),
    )


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    video_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("comments.id", ondelete="CASCADE"), nullable=True
    )
    text: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_comments_video_created", "video_id", "created_at"),
    )


class Subscription(Base):
    """3B: user -> channel subscription (channel_id IS users.id)."""

    __tablename__ = "subscriptions"

    subscriber_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_subscriptions_channel", "channel_id"),)


class VideoAcl(Base):
    __tablename__ = "video_acl"

    video_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Playlist(Base):
    """3D: a channel's playlist (FR-3). Private playlists are owner-only."""

    __tablename__ = "playlists"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_playlists_user_created", "user_id", "created_at"),)


class PlaylistItem(Base):
    """Ordered video entry; position defines the play order."""

    __tablename__ = "playlist_items"

    playlist_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("playlists.id", ondelete="CASCADE"),
        primary_key=True,
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_playlist_items_position", "playlist_id", "position"),
    )


class VideoReport(Base):
    """3E: a user report against a video (FR-7). One per (video, reporter);
    moderators resolve it with an audit trail (who/when/why)."""

    __tablename__ = "video_reports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    video_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    reporter_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    # audit journal of the moderation decision
    resolution: Mapped[str | None] = mapped_column(String(16))
    resolution_note: Mapped[str | None] = mapped_column(String(500))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('new','resolved','dismissed')", name="ck_video_reports_status"
        ),
        CheckConstraint(
            "resolution IS NULL OR resolution IN ('hide','unhide','dismiss')",
            name="ck_video_reports_resolution",
        ),
        CheckConstraint(
            "reason IN ('spam','scam','violence','hate','sexual','misleading','copyright','other')",
            name="ck_video_reports_reason",
        ),
        Index("uq_video_reports_video_reporter", "video_id", "reporter_id", unique=True),
        Index("ix_video_reports_status_created", "status", "created_at"),
    )
