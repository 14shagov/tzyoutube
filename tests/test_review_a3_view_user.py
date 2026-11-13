"""Review A3: logged-in view registration must not 500.

The /view endpoint passed `user.id` (a UUID) into
interactions.register_view, whose contract is a user OBJECT — the
service then evaluates `user.id`, i.e. `UUID.id` -> AttributeError -> 500.

The fix makes the endpoint pass the user object uniformly (None for
guests); the service already handles None. Tested through the real API
with a real `app.models.User` instance.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import videos as videos_api
from app.main import create_app
from app.models import User


def _video() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        title="Clip",
        description="d",
        status="ready",
        is_private=False,
        video_key="videos/x.mp4",
        thumbnail_key="thumbnails/x.jpg",
        duration_seconds=10,
        size_bytes=100,
        view_count=0,
    )


class FakeVideoRepo:
    def __init__(self, videos: list) -> None:
        self.by_id = {v.id: v for v in videos}

    async def get(self, video_id):
        return self.by_id.get(video_id)

    async def create(self, video):
        self.by_id[video.id] = video
        return video


class FakeViewRepo:
    """Same contract as SqlViewRepo after the A2 fix: recent_view -> bool."""

    def __init__(self) -> None:
        self.views: list = []
        self.counts: dict = {}

    async def recent_view(self, video_id, fingerprint, since) -> bool:
        return any(v[0] == video_id and v[2] == fingerprint for v in self.views)

    async def add_view(self, video_id, user_id, fingerprint):
        self.views.append((video_id, user_id, fingerprint))

    async def increment_view_count(self, video_id):
        self.counts[video_id] = self.counts.get(video_id, 0) + 1


class FakeStorage:
    def presigned_get(self, bucket, key, ttl_seconds=None):
        return f"http://example.com/{bucket}/{key}"


class FakeAclRepo:
    async def is_allowed(self, video_id, user_id):
        return False


def _real_user() -> User:
    # Real ORM type, as returned by auth resolution (not a SimpleNamespace).
    return User(
        id=uuid.uuid4(),
        email="viewer@example.com",
        password_hash="x",
        channel_name="Viewer",
        role="user",
        is_blocked=False,
    )


@pytest.fixture
def client():
    video = _video()
    app = create_app()
    user = _real_user()
    view_repo = FakeViewRepo()
    app.dependency_overrides[videos_api.get_video_repo] = lambda: FakeVideoRepo([video])
    app.dependency_overrides[videos_api.get_view_repo] = lambda: view_repo
    app.dependency_overrides[videos_api.get_storage] = lambda: FakeStorage()
    app.dependency_overrides[videos_api.get_acl] = lambda: FakeAclRepo()
    app.dependency_overrides[videos_api._optional_user] = lambda: user
    with TestClient(app) as c:
        yield c, video, view_repo, user


def test_logged_in_view_counts_with_user_id(client) -> None:
    c, video, view_repo, user = client
    r = c.post(f"/api/videos/{video.id}/view")
    assert r.status_code == 204, f"expected 204, got {r.status_code}: {r.text}"
    assert view_repo.counts.get(video.id) == 1, "view must be counted once"
    assert view_repo.views[0][1] == user.id, "view row must carry the user id"


def test_guest_view_counts_without_user_id(client) -> None:
    c, video, view_repo, _user = client
    c.app.dependency_overrides[videos_api._optional_user] = lambda: None
    r = c.post(f"/api/videos/{video.id}/view")
    assert r.status_code == 204
    assert view_repo.views[0][1] is None
    assert view_repo.views[0][2], "guest fingerprint cookie must be set"
