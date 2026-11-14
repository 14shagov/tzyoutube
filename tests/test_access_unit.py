"""Unit tests for video access control and presigned delivery.

Privacy rules (docs/security.md, FR-9): a private video is visible only
to its owner, whitelisted users (video_acl — stage 3), and staff
(moderator/admin); everyone else gets 404 (not 403, to hide existence).
"""

import uuid
from types import SimpleNamespace

import pytest

from app.services import access


def _video(is_private: bool, status: str = "ready") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        is_private=is_private,
        status=status,
    )


def _user(role: str = "user") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_blocked=False)


class FakeAclRepo:
    def __init__(self, allowed: set[tuple[uuid.UUID, uuid.UUID]]) -> None:
        self.allowed = allowed

    async def is_allowed(self, video_id, user_id) -> bool:
        return (video_id, user_id) in self.allowed


def test_public_ready_video_visible_to_guest() -> None:
    video = _video(is_private=False)
    user = None
    assert access.can_view(video, user) is True


def test_private_video_hidden_from_guest() -> None:
    video = _video(is_private=True)
    assert access.can_view(video, None) is False


def test_private_video_visible_to_owner() -> None:
    video = _video(is_private=True)
    owner = _user()
    owner.id = video.user_id
    assert access.can_view(video, owner) is True


def test_private_video_hidden_from_other_users() -> None:
    video = _video(is_private=True)
    stranger = _user()
    assert access.can_view(video, stranger) is False


def test_private_video_visible_to_moderator_and_admin() -> None:
    video = _video(is_private=True)
    assert access.can_view(video, _user(role="moderator")) is True
    assert access.can_view(video, _user(role="admin")) is True


def test_hidden_and_deleted_and_processing_not_viewable() -> None:
    for status in ("hidden", "deleted", "processing", "failed"):
        video = _video(is_private=False, status=status)
        assert access.can_view(video, None) is False, status


@pytest.mark.asyncio
async def test_acl_grants_access_to_stranger() -> None:
    video = _video(is_private=True)
    stranger = _user()
    acl = FakeAclRepo({(video.id, stranger.id)})
    assert await access.can_view_with_acl(video, stranger, acl) is True


@pytest.mark.asyncio
async def test_acl_without_entry_denies() -> None:
    video = _video(is_private=True)
    stranger = _user()
    acl = FakeAclRepo(set())
    assert await access.can_view_with_acl(video, stranger, acl) is False


def test_presigned_url_generated_only_for_allowed() -> None:
    class FakeStorage:
        def presigned_get(self, bucket, key, ttl_seconds=None, response_headers=None, private=False):
            return f"http://example.com/{bucket}/{key}?ttl={ttl_seconds}"

    storage = FakeStorage()
    video = _video(is_private=False)
    video.video_key = "videos/x.mp4"
    url = access.video_play_url(storage, video, ttl_seconds=60)
    assert "ttl=60" in url

    # Private video without a user → no URL (caller must 404 beforehand).
    private = _video(is_private=True)
    private.video_key = "videos/y.mp4"
    assert access.video_play_url(storage, private, ttl_seconds=60) is None


def test_not_ready_video_has_no_play_url() -> None:
    class FakeStorage:
        def presigned_get(self, bucket, key, ttl_seconds=None, response_headers=None, private=False):
            return "http://example.com/never"

    video = _video(is_private=False, status="processing")
    video.video_key = None
    assert access.video_play_url(FakeStorage(), video) is None


@pytest.mark.asyncio
async def test_view_or_none_combines_access_and_presign() -> None:
    class FakeStorage:
        def presigned_get(self, bucket, key, ttl_seconds=None, response_headers=None, private=False):
            return f"http://example.com/{bucket}/{key}"

    acl = FakeAclRepo(set())
    private = _video(is_private=True)
    private.video_key = "videos/p.mp4"
    stranger = _user()

    # Stranger: no URL → caller must 404.
    assert await access.view_or_none(FakeStorage(), private, stranger, acl) is None
    # Owner: URL issued.
    owner = _user()
    owner.id = private.user_id
    assert await access.view_or_none(FakeStorage(), private, owner, acl) is not None
    # ACL member: URL issued.
    acl2 = FakeAclRepo({(private.id, stranger.id)})
    assert await access.view_or_none(FakeStorage(), private, stranger, acl2) is not None
