"""Guard: ORM models vs migrations must agree on table and column names.

Parses the offline DDL emitted by `alembic upgrade head --sql` (no DB) and
compares table names and column names against Base.metadata. Catches the
"migration forgot a column / model renamed, migration didn't" drift that
unit tests on fakes never see.
"""

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _offline_ddl() -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"offline DDL generation failed:\n{proc.stderr}"
    return proc.stdout


def _migration_tables(ddl: str) -> dict[str, set[str]]:
    """table name -> column names, from CREATE TABLE blocks.

    Alembic offline mode emits fully-quoted Postgres DDL; we only extract
    names (not types), which keeps the regex stable.
    """
    tables: dict[str, set[str]] = {}
    for match in re.finditer(
        r'CREATE TABLE (\w+) \(\n(.*?)\n\);', ddl, re.DOTALL
    ):
        name = match.group(1).strip('"')
        body = match.group(2)
        columns: set[str] = set()
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line:
                continue
            # Constraint openers and table-level clauses are not columns.
            if re.match(
                r'^(PRIMARY KEY|UNIQUE|CHECK|CONSTRAINT|FOREIGN KEY|EXCLUDE)',
                line,
            ):
                continue
            col = re.match(r'^"?(\w+)"? ', line)
            if col:
                columns.add(col.group(1))
        tables[name] = columns
    return tables


def test_migration_tables_match_orm_metadata() -> None:
    from app.models import Base

    ddl = _offline_ddl()
    mig_tables = _migration_tables(ddl)

    orm_tables = {
        name: set(table.columns.keys())
        for name, table in Base.metadata.tables.items()
    }

    missing_in_migrations = set(orm_tables) - set(mig_tables)
    assert not missing_in_migrations, (
        f"tables in ORM but not in migrations: {sorted(missing_in_migrations)}"
    )

    extra_in_migrations = set(mig_tables) - set(orm_tables) - {"alembic_version"}
    assert not extra_in_migrations, (
        f"tables in migrations but not in ORM: {sorted(extra_in_migrations)}"
    )

    for tname, orm_cols in sorted(orm_tables.items()):
        mig_cols = mig_tables[tname]
        missing = orm_cols - mig_cols
        extra = mig_cols - orm_cols
        assert not missing, f"{tname}: columns in ORM but not in migrations: {sorted(missing)}"
        assert not extra, f"{tname}: columns in migrations but not in ORM: {sorted(extra)}"
