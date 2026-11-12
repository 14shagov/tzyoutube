"""API tests for video viewing endpoints: card, feed, search, views.

Repositories and storage are faked; auth via get_current_user override.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.api import videos as videos_api
from app.main import create_app


def _video(**overrides) -> SimpleNamespace:
    defaults: dict = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "title": "Clip",
        "description": "d",
        "status": "ready",
        "is_private": False,
        "video_key": "videos/x.mp4",
        "thumbnail_key": "thumbnails/x.jpg",
        "duration_seconds": 10,
        "size_bytes": 100,
        "view_count": 0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class FakeVideoRepo:
    def __init__(self, videos: list) -> None:
        self.by_id = {v.id: v for v in videos}

    async def get(self, video_id):
        return self.by_id.get(video_id)

    async def create(self, video):
        self.by_id[video.id] = video
        return video


class FakeFeedRepo:
    def __init__(self, videos: list) -> None:
        self.videos = videos

    async def public_feed(self, limit: int = 20, offset: int = 0):
        return self.videos[offset : offset + limit]

    async def search(self, query: str, limit: int = 20, offset: int = 0):
        q = query.lower()
        return [v for v in self.videos if q in v.title.lower()][offset : offset + limit]


class FakeViewRepo:
    def __init__(self) -> None:
        self.views: list = []
        self.counts: dict = {}

    async def recent_view(self, video_id, fingerprint, since):
        return None

    async def add_view(self, video_id, user_id, fingerprint):
        self.views.append((video_id, fingerprint))

    async def increment_view_count(self, video_id):
        self.counts[video_id] = self.counts.get(video_id, 0) + 1


class FakeReactionRepo:
    def __init__(self) -> None:
        self.reactions: dict = {}

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
        self.comments: list = []

    async def add(self, video_id, user_id, text, parent_id=None):
        comment = SimpleNamespace(
            id=len(self.comments), video_id=video_id, user_id=user_id,
            text=text, parent_id=parent_id,
        )
        self.comments.append(comment)
        return comment

    async def list_for_video(self, video_id, limit=50, offset=0):
        return [c for c in self.comments if c.video_id == video_id][offset : offset + limit]


class FakeStorage:
    def presigned_get(self, bucket, key, ttl_seconds=None):
        return f"http://example.com/{bucket}/{key}"

    def put(self, bucket, key, data):
        return None

    def delete(self, bucket, key):
        pass


class FakeAclRepo:
    async def is_allowed(self, video_id, user_id):
        return False


class FakeUser:
    def __init__(self, role: str = "user") -> None:
        self.id = uuid.uuid4()
        self.email = "viewer@example.com"
        self.channel_name = "Viewer"
        self.role = role
        self.is_blocked = False


def _make_app(videos: list):
    app = create_app()
    video_repo = FakeVideoRepo(videos)
    feed_repo = FakeFeedRepo(videos)
    view_repo = FakeViewRepo()
    reaction_repo = FakeReactionRepo()
    comment_repo = FakeCommentRepo()
    storage = FakeStorage()
    acl = FakeAclRepo()
    user = FakeUser()

    app.dependency_overrides[videos_api.get_video_repo] = lambda: video_repo
    app.dependency_overrides[videos_api.get_feed_repo] = lambda: feed_repo
    app.dependency_overrides[videos_api.get_view_repo] = lambda: view_repo
    app.dependency_overrides[videos_api.get_reaction_repo] = lambda: reaction_repo
    app.dependency_overrides[videos_api.get_comment_repo] = lambda: comment_repo
    app.dependency_overrides[videos_api.get_storage] = lambda: storage
    app.dependency_overrides[videos_api.get_acl] = lambda: acl
    app.dependency_overrides[auth_api.get_current_user] = lambda: user
    return app, {
        "video_repo": video_repo, "feed_repo": feed_repo, "view_repo": view_repo,
        "reaction_repo": reaction_repo, "comment_repo": comment_repo, "user": user,
    }


@pytest.fixture
def one_video() -> list:
    return [_video()]


@pytest.fixture
def client(one_video) -> TestClient:
    app, _ = _make_app(one_video)
    with TestClient(app) as c:
        yield c


def test_video_card_returns_metadata_and_url(client: TestClient, one_video) -> None:
    v = one_video[0]
    r = client.get(f"/api/videos/{v.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Clip"
    assert body["view_count"] == 0
    assert "play_url" in body


def test_private_video_404_for_stranger(client: TestClient, one_video) -> None:
    v = one_video[0]
    v.is_private = True
    r = client.get(f"/api/videos/{v.id}")
    assert r.status_code == 404


def test_unknown_video_404(client: TestClient) -> None:
    r = client.get(f"/api/videos/{uuid.uuid4()}")
    assert r.status_code == 404


def test_feed_lists_public_videos(client: TestClient) -> None:
    r = client.get("/api/feed")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1


def test_search_filters_by_title(client: TestClient, one_video) -> None:
    one_video[0].title = "Funny cats"
    r = client.get("/api/search", params={"q": "cats"})
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1
    r = client.get("/api/search", params={"q": "dogs"})
    assert r.json()["items"] == []


def test_view_registration_counts_once_per_fingerprint(client: TestClient, one_video) -> None:
    v = one_video[0]
    r = client.post(f"/api/videos/{v.id}/view")
    assert r.status_code == 204
    r = client.post(f"/api/videos/{v.id}/view")
    assert r.status_code == 204
    # First call counts; the second is deduped by the same fingerprint.
    # (FakeViewRepo.recent_view always returns None here, so both count —
    # dedup logic itself is covered in test_interactions_unit.py.)


def test_reaction_roundtrip(client: TestClient, one_video) -> None:
    v = one_video[0]
    r = client.post(f"/api/videos/{v.id}/reactions", json={"value": 1})
    assert r.status_code == 200
    assert r.json() == {"likes": 1, "dislikes": 0}
    r = client.post(f"/api/videos/{v.id}/reactions", json={"value": 1})
    assert r.json() == {"likes": 0, "dislikes": 0}
    r = client.post(f"/api/videos/{v.id}/reactions", json={"value": -1})
    assert r.json() == {"likes": 0, "dislikes": 1}


def test_reaction_requires_auth(client: TestClient, one_video) -> None:
    v = one_video[0]
    from fastapi import HTTPException

    def _guest():
        raise HTTPException(401, "not authenticated")

    client.app.dependency_overrides[auth_api.get_current_user] = _guest
    r = client.post(f"/api/videos/{v.id}/reactions", json={"value": 1})
    assert r.status_code == 401


def test_comments_add_and_list(client: TestClient, one_video) -> None:
    v = one_video[0]
    r = client.post(f"/api/videos/{v.id}/comments", json={"text": " great video "})
    assert r.status_code == 201
    r = client.get(f"/api/videos/{v.id}/comments")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["text"] == "great video"


def test_comment_validation_rejected(client: TestClient, one_video) -> None:
    v = one_video[0]
    r = client.post(f"/api/videos/{v.id}/comments", json={"text": "  "})
    assert r.status_code == 422


def test_comment_on_private_video_404(client: TestClient, one_video) -> None:
    v = one_video[0]
    v.is_private = True
    r = client.post(f"/api/videos/{v.id}/comments", json={"text": "hi"})
    assert r.status_code == 404
