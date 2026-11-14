"""Review A5/A6: comment id types and parent validation.

A5: Comment.id / parent_id are PGUUID in the DB, but CommentIn/CommentOut
declared them as `int` — creating or listing a comment raised pydantic
ValidationError -> 500. Schemas must use UUID, matching VideoCard/FeedItem.

A6 (audit item): SqlCommentRepo.add raises ValueError("invalid parent_id")
for a missing/nested parent; the endpoint must map that to 422, not 500.
"""

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.api import videos as videos_api
from app.api.videos import CommentIn, CommentOut
from app.main import create_app


def test_comment_out_accepts_uuid_id() -> None:
    cid = uuid.uuid4()
    out = CommentOut(id=cid, text="hi", parent_id=None)
    assert out.id == cid
    assert out.model_dump()["id"] == cid


def test_comment_out_accepts_uuid_parent() -> None:
    pid = uuid.uuid4()
    out = CommentOut(id=uuid.uuid4(), text="reply", parent_id=pid)
    assert out.parent_id == pid


def test_comment_in_accepts_uuid_parent() -> None:
    pid = uuid.uuid4()
    payload = CommentIn(text="reply", parent_id=pid)
    assert payload.parent_id == pid


def _video() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), user_id=uuid.uuid4(), title="Clip", description="d",
        status="ready", is_private=False, video_key="videos/x.mp4",
        thumbnail_key="thumbnails/x.jpg", duration_seconds=10, size_bytes=100,
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


class FakeCommentRepo:
    """In-memory repo; ids are UUIDs like the real Comment rows."""

    def __init__(self) -> None:
        self.comments: list = []

    async def add(self, video_id, user_id, text, parent_id=None):
        comment = SimpleNamespace(
            id=uuid.uuid4(), video_id=video_id, user_id=user_id,
            text=text, parent_id=parent_id,
        )
        self.comments.append(comment)
        return comment

    async def list_for_video(self, video_id, limit=50, offset=0):
        return [c for c in self.comments if c.video_id == video_id][offset : offset + limit]


class FakeStorage:
    def presigned_get(self, bucket, key, ttl_seconds=None, response_headers=None):
        return f"http://example.com/{bucket}/{key}"


class FakeAclRepo:
    async def is_allowed(self, video_id, user_id):
        return False


class FakeUser:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.email = "c@example.com"
        self.channel_name = "C"
        self.role = "user"
        self.is_blocked = False


def _client(comment_repo=None):
    video = _video()
    app = create_app()
    comment_repo = comment_repo or FakeCommentRepo()
    app.dependency_overrides[videos_api.get_video_repo] = lambda: FakeVideoRepo([video])
    app.dependency_overrides[videos_api.get_comment_repo] = lambda: comment_repo
    app.dependency_overrides[videos_api.get_storage] = lambda: FakeStorage()
    app.dependency_overrides[videos_api.get_acl] = lambda: FakeAclRepo()
    app.dependency_overrides[auth_api.get_current_user] = lambda: FakeUser()
    return app, video, comment_repo


def test_add_comment_returns_uuid_id() -> None:
    app, video, _repo = _client()
    with TestClient(app) as c:
        r = c.post(f"/api/videos/{video.id}/comments", json={"text": "nice"})
    assert r.status_code == 201, f"expected 201, got {r.status_code}: {r.text}"
    body = r.json()
    assert uuid.UUID(body["id"])  # must parse as UUID
    assert body["parent_id"] is None


def test_list_comments_returns_uuid_ids() -> None:
    app, video, repo = _client()
    with TestClient(app) as c:
        c.post(f"/api/videos/{video.id}/comments", json={"text": "one"})
        r = c.get(f"/api/videos/{video.id}/comments")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert uuid.UUID(items[0]["id"]) == repo.comments[0].id


class BadParentRepo(FakeCommentRepo):
    async def add(self, video_id, user_id, text, parent_id=None):
        raise ValueError("invalid parent_id")  # what SqlCommentRepo raises


def test_invalid_parent_maps_to_422_not_500() -> None:
    app, video, _repo = _client(comment_repo=BadParentRepo())
    with TestClient(app) as c:
        r = c.post(
            f"/api/videos/{video.id}/comments",
            json={"text": "reply", "parent_id": str(uuid.uuid4())},
        )
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text}"
