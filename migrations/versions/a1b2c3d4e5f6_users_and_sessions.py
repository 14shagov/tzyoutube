"""users and sessions

Revision ID: a1b2c3d4e5f6
Revises: 2363426a9ef1
Create Date: 2026-11-12 10:38:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "2363426a9ef1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create users and sessions tables (db-schema.md)."""
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("channel_name", sa.String(100), nullable=False, unique=True),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "role IN ('user', 'moderator', 'admin')", name="ck_users_role"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])


def downgrade() -> None:
    """Drop sessions and users."""
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("users")
