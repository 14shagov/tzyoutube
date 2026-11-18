"""Static checks for docs/setup.md (D4).

The setup guide must stay in sync with deploy.sh: every stage of the script,
the manual compose path, logs, update, full cleanup and the two classic
failures (port 80 busy, Docker missing).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "docs" / "setup.md"


@pytest.fixture(scope="module")
def setup() -> str:
    return SETUP.read_text(encoding="utf-8")


def test_setup_doc_exists() -> None:
    assert SETUP.is_file()


def test_has_sections(setup: str) -> None:
    headings = re.findall(r"^##\s+(.+)$", setup, re.M)
    joined = " | ".join(headings).lower()
    for topic in (
        "локальн",  # локальный запуск / порядок запуска
        "deploy",
        "compose",
        "лог",
        "обновл",
        "очистк",
        "ошиб",
    ):
        assert topic in joined, f"no section about {topic}: {headings}"


def test_local_run_order(setup: str) -> None:
    # the manual path: .env → build → up → health → seed
    for snippet in (
        ".env.example",
        "docker compose up -d --build",
        "http://localhost/health",
        "docker compose --profile seed run --rm seed",
    ):
        assert snippet in setup, snippet


def test_deploy_script_step_by_step(setup: str) -> None:
    assert "sudo ./deploy.sh" in setup
    assert "--no-seed" in setup
    # stages of deploy.sh are described one by one
    stages = re.findall(r"^\s*\d+\.\s+.+$", setup, re.M)
    assert len(stages) >= 6, stages
    for topic in ("ubuntu", "root", "docker", "80", "env", "health", "seed"):
        assert topic in setup.lower(), topic


def test_logs_section(setup: str) -> None:
    assert "docker compose logs" in setup


def test_update_section(setup: str) -> None:
    assert "git pull" in setup
    assert "docker compose up -d --build" in setup


def test_full_cleanup_section(setup: str) -> None:
    assert "docker compose down -v" in setup
    assert re.search(r"volumes?|тома|томов", setup, re.I)


def test_typical_errors(setup: str) -> None:
    low = setup.lower()
    assert "порт 80" in low or "port 80" in low
    assert "docker" in low
    # both classic failures are described with a hint
    assert "systemctl stop" in setup or "fuser -k 80/tcp" in setup
    assert "docker" in low and ("не установлен" in low or "not installed" in low)


def test_no_real_secrets_or_hosts(setup: str) -> None:
    # only documentation placeholders are allowed
    for forbidden in ("ghp_", "BEGIN RSA PRIVATE", "BEGIN OPENSSH PRIVATE"):
        assert forbidden not in setup, forbidden
    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", setup), "real IP in setup.md"
