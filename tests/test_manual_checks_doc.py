"""Static checks for the D5 documentation updates.

- docs/manual-checks.md must register the end-to-end manual scenario
  (registration → upload → view → like + comment) and a re-run of deploy.sh.
- docs/status.md must reflect the deploy/seed/README work (D0–D5) and keep
  the "done / next / problems" structure.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKS = ROOT / "docs" / "manual-checks.md"
STATUS = ROOT / "docs" / "status.md"


@pytest.fixture(scope="module")
def checks() -> str:
    return CHECKS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def status() -> str:
    return STATUS.read_text(encoding="utf-8")


def test_manual_checks_has_end_to_end_scenario(checks: str) -> None:
    headings = re.findall(r"^##\s+(.+)$", checks, re.MULTILINE)
    joined = " | ".join(headings).lower()
    assert "сценар" in joined or "e2e" in joined or "сквозн" in joined, headings
    low = checks.lower()
    for topic in ("регистрац", "загрузк", "просмотр", "лайк", "комментар"):
        assert topic in low, topic


def test_manual_checks_has_deploy_rerun(checks: str) -> None:
    assert "sudo ./deploy.sh" in checks
    headings = re.findall(r"^##\s+(.+)$", checks, re.MULTILINE)
    joined = " | ".join(headings).lower()
    assert "deploy" in joined, headings


def test_manual_checks_numbered_sections_are_continuous(checks: str) -> None:
    numbers = [
        int(n)
        for n in re.findall(r"^##\s+(\d+)\.", checks, re.MULTILINE)
    ]
    assert numbers == list(range(1, len(numbers) + 1)), numbers
    assert len(numbers) >= 20, numbers


def test_status_mentions_deploy_work(status: str) -> None:
    for topic in ("deploy.sh", "seed", "README", "127.0.0.1"):
        assert topic in status, topic


def test_status_has_done_next_problems(status: str) -> None:
    headings = re.findall(r"^##\s+(.+)$", status, re.MULTILINE)
    joined = " | ".join(headings).lower()
    assert "дальше" in joined, headings
    assert "проблем" in joined or "риск" in joined, headings


def test_status_has_update_date(status: str) -> None:
    assert re.search(r"Обновлено:\s*2026-11-18", status), "status.md not updated"
