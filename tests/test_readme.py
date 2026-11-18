"""Static checks for README.md (D3).

The README is the entry point of the one-command deploy, so its contract is
checked here instead of by hand: first line is the clone+deploy command, the
required sections exist, the links to docs/ resolve, demo accounts are
marked demo-only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


def test_readme_exists() -> None:
    assert README.is_file()


def test_first_line_is_clone_and_deploy_command(readme: str) -> None:
    first = readme.splitlines()[0].strip()
    assert first.startswith("git clone "), first
    assert " && cd " in first, first
    assert first.endswith(" && sudo ./deploy.sh"), first


def test_readme_has_short_description(readme: str) -> None:
    # a real prose paragraph (not a heading, not a code block)
    paragraphs = [
        p.strip()
        for p in readme.split("\n\n")
        if p.strip() and not p.strip().startswith(("#", "```"))
    ]
    assert paragraphs, "README is empty"
    first_paragraph = paragraphs[0]
    assert len(first_paragraph) >= 40, first_paragraph


@pytest.mark.parametrize(
    "doc",
    [
        "docs/architecture.md",
        "docs/requirements.md",
        "docs/db-schema.md",
        "docs/security.md",
        "docs/status.md",
        "docs/manual-checks.md",
    ],
)
def test_links_to_docs_resolve(readme: str, doc: str) -> None:
    assert doc in readme, f"README does not link {doc}"
    assert (ROOT / doc).is_file(), f"{doc} does not exist"


def test_architecture_section(readme: str) -> None:
    assert re.search(r"^##\s+Архитектура|^##\s+Architecture", readme, re.M)


def test_machine_requirements(readme: str) -> None:
    assert re.search(r"^##\s+.*[Тр]ребования|^##\s+.*Requirements", readme, re.M)
    assert "Ubuntu" in readme
    assert "Docker" in readme
    assert re.search(r"порт\s*80|port\s*80", readme)


def test_demo_accounts_marked_demo_only(readme: str) -> None:
    assert re.search(r"^##\s+.*(?:[Дд]емо|Demo)", readme, re.M)
    for email in ("admin@example.com", "mod@example.com", "user1@example.com"):
        assert email in readme, email
    assert re.search(r"demo[- ]only|только для демо|демо[- ]?только", readme, re.I)


def test_addresses(readme: str) -> None:
    assert "http://localhost/" in readme
    assert "http://localhost/docs" in readme


def test_test_command(readme: str) -> None:
    assert "uv run pytest -q" in readme


def test_repo_structure(readme: str) -> None:
    assert re.search(r"^##\s+.*[Ст]труктура|^##\s+.*[Rr]epository structure", readme, re.M)
    for path in (
        "src/app/",
        "src/worker/",
        "tests/",
        "migrations/",
        "scripts/seed.py",
        "nginx/",
        "docs/",
        "docker-compose.yml",
        "deploy.sh",
    ):
        assert path in readme, path
