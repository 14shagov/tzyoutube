"""video domain tables: videos, video_views, reactions, comments, video_acl

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-11-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "b7c8d9e0f1a2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def _uuid() -> sa.Column:
    # Named explicitly: an unnamed Column raises ArgumentError when added
    # to a Table (caught on the real stack, now guarded by offline tests).
    return sa.Column(
        "id", PGUUID(as_uuid=True), primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def upgrade() -> None:
    op.create_table(
        "videos",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", PGUUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_private", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="processing"),
        sa.Column("raw_key", sa.String(200), nullable=False),
        sa.Column("video_key", sa.String(200), nullable=True),
        sa.Column("thumbnail_key", sa.String(200), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("view_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('processing','ready','failed','hidden','deleted')",
            name="ck_videos_status",
        ),
    )
    op.create_index("ix_videos_status_created", "videos", ["status", "created_at"])
    op.create_index("ix_videos_user_created", "videos", ["user_id", "created_at"])
    op.create_index(
        "ix_videos_public_feed", "videos", ["created_at"],
        postgresql_where=sa.text("status = 'ready' AND NOT is_private"),
    )

    op.create_table(
        "video_views",
        _uuid(),
        sa.Column("video_id", PGUUID(as_uuid=True),
                  sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", PGUUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("viewer_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_video_views_video_fp", "video_views",
                    ["video_id", "viewer_fingerprint"])

    op.create_table(
        "reactions",
        _uuid(),
        sa.Column("user_id", PGUUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("video_id", PGUUID(as_uuid=True),
                  sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("value IN (1, -1)", name="ck_reactions_value"),
        sa.UniqueConstraint("user_id", "video_id", name="uq_reactions_user_video"),
    )

    op.create_table(
        "comments",
        _uuid(),
        sa.Column("video_id", PGUUID(as_uuid=True),
                  sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", PGUUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", PGUUID(as_uuid=True),
                  sa.ForeignKey("comments.id", ondelete="CASCADE"), nullable=True),
        sa.Column("text", sa.String(2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_comments_video_created", "comments", ["video_id", "created_at"])

    op.create_table(
        "video_acl",
        sa.Column("video_id", PGUUID(as_uuid=True),
                  sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", PGUUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("video_acl")
    op.drop_index("ix_comments_video_created", table_name="comments")
    op.drop_table("comments")
    op.drop_table("reactions")
    op.drop_index("ix_video_views_video_fp", table_name="video_views")
    op.drop_table("video_views")
    op.drop_index("ix_videos_public_feed", table_name="videos")
    op.drop_index("ix_videos_user_created", table_name="videos")
    op.drop_index("ix_videos_status_created", table_name="videos")
    op.drop_table("videos")
