"""Tests for Alembic setup: config parses, script location exists, revisions valid."""

import configparser
import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> configparser.ConfigParser:
    # interpolation=None: script_location contains the literal %(here)s token
    cp = configparser.ConfigParser(interpolation=None)
    assert cp.read(PROJECT_ROOT / "alembic.ini", encoding="utf-8")
    return cp


def test_alembic_ini_points_to_migrations() -> None:
    cp = _alembic_config()
    script_location = cp.get("alembic", "script_location")
    resolved = script_location.replace("%(here)s", str(PROJECT_ROOT))
    assert Path(resolved).is_dir()


def test_migration_scripts_are_valid_python() -> None:
    versions_dir = PROJECT_ROOT / "migrations" / "versions"
    assert versions_dir.is_dir()
    scripts = list(versions_dir.glob("*.py"))
    assert scripts, "expected at least one migration script"
    for script in scripts:
        spec = importlib.util.spec_from_file_location(script.stem, script)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision
        assert module.down_revision is None or isinstance(module.down_revision, str)
