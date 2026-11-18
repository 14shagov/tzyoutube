"""Static checks for AGENTS.md (D5).

AGENTS.md is the launch contract for every agent/session working in this repo:
it must name the actual commands — one-command deploy, compose operations,
seed, tests, lint — and keep pointing at the current docs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"


@pytest.fixture(scope="module")
def agents() -> str:
    return AGENTS.read_text(encoding="utf-8")


def test_agents_exists() -> None:
    assert AGENTS.is_file()


def test_has_launch_section(agents: str) -> None:
    headings = re.findall(r"^##\s+(.+)$", agents, re.MULTILINE)
    joined = " | ".join(headings).lower()
    assert "запуск" in joined or "launch" in joined, headings


def test_launch_commands_present(agents: str) -> None:
    for snippet in (
        "sudo ./deploy.sh",
        "sudo ./deploy.sh --no-seed",
        "docker compose up -d --build",
        "docker compose --profile seed run --rm seed",
        "docker compose logs",
        "docker compose down -v",
        "uv run pytest",
        "uv run ruff check .",
    ):
        assert snippet in agents, snippet


def test_points_to_setup_and_status_docs(agents: str) -> None:
    assert "docs/setup.md" in agents
    assert "docs/status.md" in agents


def test_keeps_uv_only_rule_and_no_local_docker(agents: str) -> None:
    assert "Запрещены `pip install`" in agents
    assert "Docker и Docker Compose локально не запускать" in agents
