"""Review B1: pagination params on every list endpoint must be bounded.

`limit` must satisfy 0 <= limit <= 100 and `offset` must satisfy offset >= 0
on /api/feed, /api/search and /api/videos/{id}/comments. Out-of-range values
must be rejected with 422 by request validation, never reach the repository
(an unbounded LIMIT/OFFSET is a trivial DoS vector).
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import videos as videos_api
from app.main import create_app


def _video() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        title="Clip",
        description="d",
        status="ready",
        is_private=False,
        video_key="videos/x.mp4",
        thumbnail_key=None,
        duration_seconds=10,
        size_bytes=100,
        view_count=0,
    )


class FakeVideoRepo:
    def __init__(self, videos: list) -> None:
        self.by_id = {v.id: v for v in videos}

    async def get(self, video_id):
        return self.by_id.get(video_id)


class FakeFeedRepo:
    def __init__(self, videos: list) -> None:
        self.videos = videos
        self.calls: list[tuple] = []

    async def public_feed(self, limit: int = 20, offset: int = 0):
        self.calls.append(("feed", limit, offset))
        return self.videos[offset : offset + limit]

    async def search(self, query: str, limit: int = 20, offset: int = 0):
        self.calls.append(("search", query, limit, offset))
        q = query.lower()
        return [v for v in self.videos if q in v.title.lower()][offset : offset + limit]


class FakeCommentRepo:
    async def list_for_video(self, video_id, limit=50, offset=0):
        return []


class FakeAclRepo:
    async def is_allowed(self, video_id, user_id):
        return False


@pytest.fixture
def client() -> TestClient:
    video = _video()
    app = create_app()
    app.dependency_overrides[videos_api.get_video_repo] = lambda: FakeVideoRepo([video])
    app.dependency_overrides[videos_api.get_feed_repo] = lambda: FakeFeedRepo([video])
    app.dependency_overrides[videos_api.get_comment_repo] = lambda: FakeCommentRepo()
    app.dependency_overrides[videos_api.get_acl] = lambda: FakeAclRepo()
    with TestClient(app) as c:
        c.video = video  # type: ignore[attr-defined]
        yield c


class TestFeedPagination:
    def test_negative_limit_rejected_422(self, client: TestClient) -> None:
        assert client.get("/api/feed", params={"limit": -1}).status_code == 422

    def test_limit_above_100_rejected_422(self, client: TestClient) -> None:
        assert client.get("/api/feed", params={"limit": 101}).status_code == 422

    def test_negative_offset_rejected_422(self, client: TestClient) -> None:
        assert client.get("/api/feed", params={"offset": -1}).status_code == 422

    def test_boundary_values_accepted(self, client: TestClient) -> None:
        assert client.get("/api/feed", params={"limit": 0}).status_code == 200
        assert client.get("/api/feed", params={"limit": 100, "offset": 0}).status_code == 200


class TestSearchPagination:
    def test_limit_above_100_rejected_422(self, client: TestClient) -> None:
        r = client.get("/api/search", params={"q": "clip", "limit": 1000})
        assert r.status_code == 422

    def test_negative_offset_rejected_422(self, client: TestClient) -> None:
        r = client.get("/api/search", params={"q": "clip", "offset": -5})
        assert r.status_code == 422


class TestCommentsPagination:
    def test_limit_above_100_rejected_422(self, client: TestClient) -> None:
        r = client.get(f"/api/videos/{client.video.id}/comments", params={"limit": 101})
        assert r.status_code == 422

    def test_negative_offset_rejected_422(self, client: TestClient) -> None:
        r = client.get(f"/api/videos/{client.video.id}/comments", params={"offset": -1})
        assert r.status_code == 422

    def test_boundary_limit_accepted(self, client: TestClient) -> None:
        r = client.get(f"/api/videos/{client.video.id}/comments", params={"limit": 100})
        assert r.status_code == 200
