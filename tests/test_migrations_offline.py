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
        "reactions", "comments", "video_acl", "alembic_version",
    ):
        assert table in sql, f"table {table!r} missing from offline DDL"


def test_offline_ddl_has_no_inline_errors() -> None:
    proc = _run_alembic_offline()
    assert proc.returncode == 0, f"offline upgrade failed:\n{proc.stderr}"
    for marker in ("Traceback", "ArgumentError", "NameError"):
        assert marker not in proc.stderr, f"error marker {marker!r} in stderr"
