"""Unit tests for the interactions service: views dedup, reactions, comments.

Repository protocols are faked; dedup logic is pure and time-aware
(clock injected for tests).
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import interactions as ix


def _now() -> datetime:
    return datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)


class FakeViewRepo:
    def __init__(self) -> None:
        self.views: list[SimpleNamespace] = []
        self.counts: dict[uuid.UUID, int] = {}

    async def recent_view(self, video_id, fingerprint, since):
        return next(
            (
                v
                for v in self.views
                if v.video_id == video_id
                and v.viewer_fingerprint == fingerprint
                and v.created_at >= since
            ),
            None,
        )

    async def add_view(self, video_id, user_id, fingerprint):
        self.views.append(
            SimpleNamespace(
                video_id=video_id, user_id=user_id,
                viewer_fingerprint=fingerprint, created_at=_now(),
            )
        )

    async def increment_view_count(self, video_id):
        self.counts[video_id] = self.counts.get(video_id, 0) + 1


class FakeReactionRepo:
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


class FakeCommentRepo:
    def __init__(self) -> None:
        self.comments: list[SimpleNamespace] = []
        self._next_id = 0

    async def add(self, video_id, user_id, text, parent_id=None):
        comment = SimpleNamespace(
            id=self._next_id, video_id=video_id, user_id=user_id,
            text=text, parent_id=parent_id, created_at=_now(),
        )
        self._next_id += 1
        self.comments.append(comment)
        return comment

    async def list_for_video(self, video_id, limit=50, offset=0):
        items = [c for c in self.comments if c.video_id == video_id]
        return items[offset : offset + limit]


@pytest.mark.asyncio
async def test_first_view_counts_second_within_24h_deduped() -> None:
    repo = FakeViewRepo()
    video_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())

    counted1 = await ix.register_view(repo, video_id, user, "fp-1", clock=_now)
    counted2 = await ix.register_view(repo, video_id, user, "fp-1", clock=_now)
    assert counted1 is True
    assert counted2 is False
    assert repo.counts[video_id] == 1


@pytest.mark.asyncio
async def test_view_after_24h_counts_again() -> None:
    repo = FakeViewRepo()
    video_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())

    await ix.register_view(repo, video_id, user, "fp-1", clock=_now)
    counted = await ix.register_view(
        repo, video_id, user, "fp-1", clock=lambda: _now() + timedelta(hours=25)
    )
    assert counted is True
    assert repo.counts[video_id] == 2


@pytest.mark.asyncio
async def test_different_fingerprints_both_count() -> None:
    repo = FakeViewRepo()
    video_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())

    await ix.register_view(repo, video_id, user, "fp-1", clock=_now)
    counted = await ix.register_view(repo, video_id, None, "fp-2", clock=_now)
    assert counted is True
    assert repo.counts[video_id] == 2


@pytest.mark.asyncio
async def test_reaction_toggles_like_dislike_and_off() -> None:
    repo = FakeReactionRepo()
    user_id, video_id = uuid.uuid4(), uuid.uuid4()

    await ix.set_reaction(repo, user_id, video_id, 1)   # like
    assert repo.reactions[(user_id, video_id)] == 1
    await ix.set_reaction(repo, user_id, video_id, -1)  # switch to dislike
    assert repo.reactions[(user_id, video_id)] == -1
    await ix.set_reaction(repo, user_id, video_id, 1)   # back to like
    assert repo.reactions[(user_id, video_id)] == 1
    await ix.set_reaction(repo, user_id, video_id, 0)   # 0 → remove (toggle off)
    assert (user_id, video_id) not in repo.reactions


@pytest.mark.asyncio
async def test_comment_text_validation() -> None:
    repo = FakeCommentRepo()
    video_id, user_id = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(ix.InvalidComment):
        await ix.add_comment(repo, video_id, user_id, "   ")
    with pytest.raises(ix.InvalidComment):
        await ix.add_comment(repo, video_id, user_id, "x" * 2001)
    comment = await ix.add_comment(repo, video_id, user_id, " hello ")
    assert comment.text == "hello"
