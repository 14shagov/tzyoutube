"""video_reports table (3E, FR-7)

One report per (video, reporter) — enforced by a unique index. Moderation
decisions are journaled on the row itself: resolution (hide/unhide/dismiss),
free-text note, who resolved it and when.

Revision ID: b1c2d3e4f5a6
Revises: f1a2b3c4d5e6
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b1c2d3e4f5a6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None

_REASONS = (
    "'spam','scam','violence','hate','sexual','misleading','copyright','other'"
)


def upgrade() -> None:
    op.create_table(
        "video_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reporter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="new"),
        sa.Column("resolution", sa.String(length=16), nullable=True),
        sa.Column("resolution_note", sa.String(length=500), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('new','resolved','dismissed')", name="ck_video_reports_status"
        ),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN ('hide','unhide','dismiss')",
            name="ck_video_reports_resolution",
        ),
        sa.CheckConstraint(
            f"reason IN ({_REASONS})", name="ck_video_reports_reason"
        ),
    )
    op.create_index(
        "uq_video_reports_video_reporter",
        "video_reports",
        ["video_id", "reporter_id"],
        unique=True,
    )
    op.create_index(
        "ix_video_reports_status_created",
        "video_reports",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_video_reports_status_created", table_name="video_reports")
    op.drop_index("uq_video_reports_video_reporter", table_name="video_reports")
    op.drop_table("video_reports")
