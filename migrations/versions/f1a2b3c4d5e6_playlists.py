"""playlists + playlist_items tables (3D, FR-3)

A playlist belongs to a channel (users row); items are (playlist, video)
pairs with a position that defines the order. Deleting a playlist or a
video cascades to its items.

Revision ID: f1a2b3c4d5e6
Revises: e1f2a3b4c5d6
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f1a2b3c4d5e6"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "playlists",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("is_private", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_playlists_user_created", "playlists", ["user_id", "created_at"], unique=False
    )
    op.create_table(
        "playlist_items",
        sa.Column("playlist_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["playlist_id"], ["playlists.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("playlist_id", "video_id"),
    )
    op.create_index(
        "ix_playlist_items_position",
        "playlist_items",
        ["playlist_id", "position"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_playlist_items_position", table_name="playlist_items")
    op.drop_table("playlist_items")
    op.drop_index("ix_playlists_user_created", table_name="playlists")
    op.drop_table("playlists")
