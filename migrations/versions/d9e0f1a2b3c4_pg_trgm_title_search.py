"""enable pg_trgm + GIN trigram index on videos.title (D1)

Search (SqlFeedRepo.search, ILIKE on title) becomes index-supported.
pg_trgm ships in postgresql-contrib, which the official postgres:16-alpine
image already includes (docker-compose pins postgres:16-alpine), so
CREATE EXTENSION works with no extra packages.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-11-14
"""

from alembic import op

revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_videos_title_trgm",
        "videos",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_videos_title_trgm", table_name="videos")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
