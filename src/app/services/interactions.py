"""Interactions domain: view counting with dedup, reactions, comments.

Rules (docs/requirements.md):
- FR-2.3: a view is counted once per viewer (user id or guest cookie
  fingerprint) per 24 hours; duplicates do not increment view_count.
- FR-3 (comments): one nesting level, text 1..2000 chars, trimmed.
- Reactions: like/dislike toggle — same value again removes the reaction.
"""

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

VIEW_DEDUP_WINDOW = timedelta(hours=24)
COMMENT_MAX_LENGTH = 2000


class InvalidComment(Exception):
    """Comment text failed validation."""


class ViewRepo(Protocol):
    async def recent_view(self, video_id, fingerprint, since: datetime): ...

    async def add_view(self, video_id, user_id, fingerprint: str) -> None: ...

    async def increment_view_count(self, video_id) -> None: ...


class ReactionRepo(Protocol):
    async def get(self, user_id, video_id) -> int | None: ...

    async def upsert(self, user_id, video_id, value: int) -> None: ...

    async def delete(self, user_id, video_id) -> None: ...

    async def counts(self, video_id) -> tuple[int, int]: ...


class CommentRepo(Protocol):
    async def add(self, video_id, user_id, text: str, parent_id=None): ...

    async def list_for_video(self, video_id, limit: int = 50, offset: int = 0): ...


async def register_view(
    repo: ViewRepo,
    video_id: uuid.UUID,
    user,
    fingerprint: str,
    clock: Callable[[], datetime] | None = None,
) -> bool:
    """Count a view unless this viewer was already seen within 24 hours.

    Returns True when the view was counted. The caller supplies the
    fingerprint: user id string for authenticated viewers, guest cookie
    otherwise (FR-2.3). `clock` is injected for tests.
    """
    now = clock() if clock is not None else datetime.now(tz=None)
    since = now - VIEW_DEDUP_WINDOW
    if await repo.recent_view(video_id, fingerprint, since) is not None:
        return False
    user_id = user.id if user is not None else None
    await repo.add_view(video_id, user_id, fingerprint)
    await repo.increment_view_count(video_id)
    return True


async def set_reaction(
    repo: ReactionRepo,
    user_id: uuid.UUID,
    video_id: uuid.UUID,
    value: int,
) -> None:
    """Set like (1) / dislike (-1); same value again removes the reaction.

    `value=0` explicitly removes whatever reaction exists.
    """
    current = await repo.get(user_id, video_id)
    if value == 0 or current == value:
        await repo.delete(user_id, video_id)
        return
    await repo.upsert(user_id, video_id, value)


async def add_comment(
    repo: CommentRepo,
    video_id: uuid.UUID,
    user_id: uuid.UUID,
    text: str,
    parent_id=None,
):
    """Add a comment (one nesting level: parent must be top-level)."""
    stripped = (text or "").strip()
    if not stripped or len(stripped) > COMMENT_MAX_LENGTH:
        raise InvalidComment("comment text must be 1..2000 chars")
    return await repo.add(video_id, user_id, stripped, parent_id)
