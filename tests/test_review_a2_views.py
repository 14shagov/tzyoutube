"""Review A2: view dedup contract.

SqlViewRepo.recent_view returns a bool (True = viewer seen within the
window). The service must treat that as the contract: truthy -> dedup,
falsy -> count. Previously the service checked `is not None`, which is
always True for a bool, so NO view was ever counted.

Scenario required by the review: first view counts, repeat within 24 h
does not, view after the window counts again.
"""

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.repos import SqlViewRepo
from app.services import interactions as ix


def _now() -> datetime:
    return datetime(2026, 11, 13, 12, 0, 0)


class BoolViewRepo:
    """In-memory repo with the SAME contract as SqlViewRepo: bool result."""

    def __init__(self, clock) -> None:
        self._clock = clock
        self.views: list[SimpleNamespace] = []
        self.counts: dict[uuid.UUID, int] = {}

    async def recent_view(self, video_id, fingerprint, since) -> bool:
        return any(
            v.video_id == video_id
            and v.viewer_fingerprint == fingerprint
            and v.created_at >= since
            for v in self.views
        )

    async def add_view(self, video_id, user_id, fingerprint) -> None:
        self.views.append(
            SimpleNamespace(
                video_id=video_id,
                user_id=user_id,
                viewer_fingerprint=fingerprint,
                created_at=self._clock(),
            )
        )

    async def increment_view_count(self, video_id) -> None:
        self.counts[video_id] = self.counts.get(video_id, 0) + 1


@pytest.mark.asyncio
async def test_first_view_counted_repeat_within_24h_deduped() -> None:
    repo = BoolViewRepo(clock=_now)
    video_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())

    counted1 = await ix.register_view(repo, video_id, user, "fp-1", clock=_now)
    counted2 = await ix.register_view(repo, video_id, user, "fp-1", clock=_now)

    assert counted1 is True, "first view within 24 h window must be counted"
    assert counted2 is False, "repeat inside the 24 h window must be deduped"
    assert repo.counts[video_id] == 1


@pytest.mark.asyncio
async def test_view_after_24h_window_counts_again() -> None:
    repo = BoolViewRepo(clock=_now)
    video_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())

    await ix.register_view(repo, video_id, user, "fp-1", clock=_now)
    def later() -> datetime:
        return _now() + timedelta(hours=25)

    counted = await ix.register_view(repo, video_id, user, "fp-1", clock=later)

    assert counted is True, "view after the 24 h window must count again"
    assert repo.counts[video_id] == 2


def test_sql_view_repo_recent_view_returns_bool_row_present() -> None:
    """Pin the SQL side of the contract: recent_view must return bool."""
    import asyncio

    session = create_autospec(AsyncSession, instance=True)
    session.execute.return_value.first = lambda: (1,)  # a matching row exists
    repo = SqlViewRepo(session)
    result = asyncio.run(repo.recent_view(uuid.uuid4(), "fp", _now()))
    assert result is True
    assert isinstance(result, bool)


def test_sql_view_repo_recent_view_returns_bool_row_absent() -> None:
    import asyncio

    session = create_autospec(AsyncSession, instance=True)
    session.execute.return_value.first = lambda: None
    repo = SqlViewRepo(session)
    result = asyncio.run(repo.recent_view(uuid.uuid4(), "fp", _now()))
    assert result is False
    assert isinstance(result, bool)
