"""add videos.failure_reason — a failed transcode keeps the reason (C2)

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-11-14
"""

from alembic import op
import sqlalchemy as sa

revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("failure_reason", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "failure_reason")
