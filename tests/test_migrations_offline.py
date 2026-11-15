"""Static guards for migration scripts (no Docker / no database).

These tests complement test_migrations.py: they execute every migration
offline (`alembic upgrade head --sql`) so that structural errors — like a
Column without a name — fail loudly in unit runs, not on the real stack.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_alembic_offline() -> subprocess.CompletedProcess:
    """alembic upgrade head --sql: emit full DDL without any DBAPI."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_offline_upgrade_generates_ddl_for_all_migrations() -> None:
    proc = _run_alembic_offline()
    assert proc.returncode == 0, (
        f"offline upgrade failed:\n{proc.stdout}\n{proc.stderr}"
    )
    sql = proc.stdout
    # Every table from db-schema.md must appear in the emitted DDL.
    for table in (
        "users", "sessions", "videos", "video_views",
        "reactions", "comments", "video_acl", "subscriptions",
        "playlists", "playlist_items", "video_reports", "alembic_version",
    ):
        assert table in sql, f"table {table!r} missing from offline DDL"
    # 3A: channel description lives on users.
    assert 'ALTER TABLE users ADD COLUMN channel_description' in sql
    # 3D: playlists.
    assert "CREATE TABLE playlists" in sql
    assert "CREATE TABLE playlist_items" in sql
    # 3E: moderation reports, one per (video, reporter), audit columns.
    assert "CREATE TABLE video_reports" in sql
    assert "uq_video_reports_video_reporter" in sql
    assert "resolved_by" in sql and "resolution_note" in sql


def test_offline_ddl_has_no_inline_errors() -> None:
    proc = _run_alembic_offline()
    assert proc.returncode == 0, f"offline upgrade failed:\n{proc.stderr}"
    for marker in ("Traceback", "ArgumentError", "NameError"):
        assert marker not in proc.stderr, f"error marker {marker!r} in stderr"


def test_offline_ddl_enables_pg_trgm_and_gin_title_index() -> None:
    """D1: trigram search support — extension + GIN index on videos.title."""
    proc = _run_alembic_offline()
    assert proc.returncode == 0, f"offline upgrade failed:\n{proc.stderr}"
    sql = proc.stdout.upper()
    assert "CREATE EXTENSION IF NOT EXISTS PG_TRGM" in sql
    assert "USING GIN" in sql and "GIN_TRGM_OPS" in sql
    assert "IX_VIDEOS_TITLE_TRGM" in sql
