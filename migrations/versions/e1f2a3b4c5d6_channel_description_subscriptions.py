"""users.channel_description + subscriptions table (3A/3B, FR-3)

Channel profile gains a description; subscriptions store (subscriber,
channel) pairs with a PK on the pair (idempotent subscribe) and an index
on channel_id for subscriber counts.

Revision ID: e1f2a3b4c5d6
Revises: d9e0f1a2b3c4
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e1f2a3b4c5d6"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("channel_description", sa.Text(), server_default="", nullable=False),
    )
    op.create_table(
        "subscriptions",
        sa.Column("subscriber_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["subscriber_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["channel_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("subscriber_id", "channel_id"),
    )
    op.create_index(
        "ix_subscriptions_channel", "subscriptions", ["channel_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_subscriptions_channel", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_column("users", "channel_description")
