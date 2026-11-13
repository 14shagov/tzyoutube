"""Review A4: like/dislike toggle contract.

ReactionRepo protocol: get(user_id, video_id) -> int | None.
SqlReactionRepo.get returned the whole Reaction row, so the service's
`current == value` compared a Reaction object to an int — always False —
and a repeated like could never toggle off.

Required scenarios: like, repeated like removes, like -> dislike switch,
value=0 removes, counters correct.
"""

import asyncio
import uuid
from unittest.mock import create_autospec

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.repos import SqlReactionRepo
from app.models import Reaction
from app.services import interactions as ix


def _session_with_reaction(value: int | None):
    """Autospec'd AsyncSession whose SELECT ... scalar_one_or_none() yields
    a Reaction row with `value` (None = no row)."""
    session = create_autospec(AsyncSession, instance=True)
    row = Reaction(user_id=uuid.uuid4(), video_id=uuid.uuid4(), value=value) \
        if value is not None else None
    session.execute.return_value.scalar_one_or_none = lambda: row
    return session, row


def test_sql_reaction_repo_get_returns_int_not_row() -> None:
    session, _row = _session_with_reaction(1)
    repo = SqlReactionRepo(session)
    result = asyncio.run(repo.get(uuid.uuid4(), uuid.uuid4()))
    assert result == 1, f"get() must return the int value, got {result!r}"
    assert isinstance(result, int)


def test_sql_reaction_repo_get_returns_none_when_absent() -> None:
    session, _row = _session_with_reaction(None)
    repo = SqlReactionRepo(session)
    result = asyncio.run(repo.get(uuid.uuid4(), uuid.uuid4()))
    assert result is None


def test_sql_reaction_repo_upsert_updates_existing_row() -> None:
    session, row = _session_with_reaction(1)
    repo = SqlReactionRepo(session)
    asyncio.run(repo.upsert(uuid.uuid4(), uuid.uuid4(), -1))
    assert row is not None and row.value == -1, "existing row must be updated in place"


class IntReactionRepo:
    """In-memory repo honouring the protocol: get -> int | None."""

    def __init__(self) -> None:
        self.reactions: dict[tuple[uuid.UUID, uuid.UUID], int] = {}

    async def get(self, user_id, video_id):
        return self.reactions.get((user_id, video_id))

    async def upsert(self, user_id, video_id, value):
        self.reactions[(user_id, video_id)] = value

    async def delete(self, user_id, video_id):
        self.reactions.pop((user_id, video_id), None)

    async def counts(self, video_id):
        likes = sum(1 for v in self.reactions.values() if v == 1)
        dislikes = sum(1 for v in self.reactions.values() if v == -1)
        return likes, dislikes


@pytest.mark.asyncio
async def test_like_then_repeat_like_toggles_off() -> None:
    repo = IntReactionRepo()
    user_id, video_id = uuid.uuid4(), uuid.uuid4()
    await ix.set_reaction(repo, user_id, video_id, 1)
    assert await repo.counts(video_id) == (1, 0)
    await ix.set_reaction(repo, user_id, video_id, 1)  # same value again -> remove
    assert (user_id, video_id) not in repo.reactions
    assert await repo.counts(video_id) == (0, 0)


@pytest.mark.asyncio
async def test_like_switches_to_dislike() -> None:
    repo = IntReactionRepo()
    user_id, video_id = uuid.uuid4(), uuid.uuid4()
    await ix.set_reaction(repo, user_id, video_id, 1)
    await ix.set_reaction(repo, user_id, video_id, -1)
    assert repo.reactions[(user_id, video_id)] == -1
    assert await repo.counts(video_id) == (0, 1)


@pytest.mark.asyncio
async def test_value_zero_removes_existing_reaction() -> None:
    repo = IntReactionRepo()
    user_id, video_id = uuid.uuid4(), uuid.uuid4()
    await ix.set_reaction(repo, user_id, video_id, -1)
    await ix.set_reaction(repo, user_id, video_id, 0)
    assert (user_id, video_id) not in repo.reactions
    assert await repo.counts(video_id) == (0, 0)


@pytest.mark.asyncio
async def test_counts_aggregate_across_users() -> None:
    repo = IntReactionRepo()
    video_id = uuid.uuid4()
    await ix.set_reaction(repo, uuid.uuid4(), video_id, 1)
    await ix.set_reaction(repo, uuid.uuid4(), video_id, 1)
    await ix.set_reaction(repo, uuid.uuid4(), video_id, -1)
    assert await repo.counts(video_id) == (2, 1)
