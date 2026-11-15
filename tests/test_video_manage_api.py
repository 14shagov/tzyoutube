"""3C red tests: video management (FR-10), ACL whitelist (FR-9), worker delete job.

PATCH/DELETE /api/videos/{id} — owner only (staff may delete), soft delete
GET /api/me/videos            — my videos incl. private + failure_reason
GET/POST/DELETE /api/videos/{id}/acl — owner only, add by email
Worker must register a "delete_video" task for the enqueued job.
"""

import inspect
import re
import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api import videos as videos_api
from app.api.auth import get_current_user, get_user_repo
from app.main import create_app
from app.models import User, Video
from test_channels_api import _user


def _video(owner_id, title="v", status="ready", is_private=False, failure_reason=None) -> Video:
    return Video(
        id=uuid.uuid4(),
        user_id=owner_id,
        title=title,
        description="d",
        is_private=is_private,
        status=status,
        raw_key="raw/x",
        failure_reason=failure_reason,
        duration_seconds=5,
        size_bytes=10,
        view_count=1,
        created_at=datetime.now(UTC),
    )


class FakeVideoRepo:
    def __init__(self, videos=()):
        self.videos = {v.id: v for v in videos}

    async def get(self, video_id):
        return self.videos.get(video_id)

    async def update(self, video):
        self.videos[video.id] = video
        return video

    async def list_user_videos(self, user_id, *, limit, offset):
        mine = [
            v for v in self.videos.values()
            if v.user_id == user_id and v.status != "deleted"
        ]
        return mine[offset : offset + limit]


class FakeQueue:
    def __init__(self):
        self.enqueued: list[tuple[str, uuid.UUID]] = []

    async def enqueue_transcode(self, video_id):
        self.enqueued.append(("transcode", video_id))

    async def enqueue_delete(self, video_id):
        self.enqueued.append(("delete_video", video_id))


class FakeAclRepo:
    def __init__(self):
        self.entries: dict[uuid.UUID, dict[uuid.UUID, User]] = {}

    async def is_allowed(self, video_id, user_id):
        return user_id in self.entries.get(video_id, {})

    async def add(self, video_id, user_id):
        self.entries.setdefault(video_id, {})[user_id] = True

    async def remove(self, video_id, user_id):
        self.entries.get(video_id, {}).pop(user_id, None)

    async def list_entries(self, video_id):
        return list(self.entries.get(video_id, {}).values())


class FakeUserRepo:
    def __init__(self, users=()):
        self.users = {u.email: u for u in users}

    async def get_by_email(self, email):
        return self.users.get(email)


def _client(video_repo, queue, acl, user_repo=None, user=None):
    app = create_app()
    app.dependency_overrides[videos_api.get_video_repo] = lambda: video_repo
    app.dependency_overrides[videos_api.get_queue] = lambda: queue
    app.dependency_overrides[videos_api.get_acl] = lambda: acl
    if user_repo is not None:
        app.dependency_overrides[get_user_repo] = lambda: user_repo
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


# --- PATCH ------------------------------------------------------------------


def test_patch_video_owner_ok() -> None:
    owner = _user()
    video = _video(owner.id)
    client = _client(FakeVideoRepo([video]), FakeQueue(), FakeAclRepo(), user=owner)
    resp = client.patch(
        f"/api/videos/{video.id}",
        json={"title": "New", "description": "dd", "is_private": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "New" and body["description"] == "dd"
    assert body["is_private"] is True and body["status"] == "ready"
    assert video.title == "New" and video.is_private is True


def test_patch_video_non_owner_403() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    video = _video(owner.id)
    client = _client(FakeVideoRepo([video]), FakeQueue(), FakeAclRepo(), user=other)
    resp = client.patch(f"/api/videos/{video.id}", json={"title": "hax"})
    assert resp.status_code == 403


def test_patch_video_unknown_404() -> None:
    owner = _user()
    client = _client(FakeVideoRepo(), FakeQueue(), FakeAclRepo(), user=owner)
    assert client.patch(f"/api/videos/{uuid.uuid4()}", json={"title": "x"}).status_code == 404


def test_patch_video_deleted_404() -> None:
    owner = _user()
    video = _video(owner.id, status="deleted")
    client = _client(FakeVideoRepo([video]), FakeQueue(), FakeAclRepo(), user=owner)
    assert client.patch(f"/api/videos/{video.id}", json={"title": "x"}).status_code == 404


def test_patch_video_guest_401() -> None:
    owner = _user()
    video = _video(owner.id)
    client = _client(FakeVideoRepo([video]), FakeQueue(), FakeAclRepo(), user=None)
    assert client.patch(f"/api/videos/{video.id}", json={"title": "x"}).status_code == 401


def test_patch_video_title_bounds_422() -> None:
    owner = _user()
    video = _video(owner.id)
    client = _client(FakeVideoRepo([video]), FakeQueue(), FakeAclRepo(), user=owner)
    assert client.patch(
        f"/api/videos/{video.id}", json={"title": " "}
    ).status_code == 422
    assert client.patch(
        f"/api/videos/{video.id}", json={"title": "x" * 201}
    ).status_code == 422


# --- DELETE (soft) ------------------------------------------------------------


def test_delete_video_owner_soft_deletes_and_enqueues() -> None:
    owner = _user()
    video = _video(owner.id)
    queue = FakeQueue()
    client = _client(FakeVideoRepo([video]), queue, FakeAclRepo(), user=owner)
    resp = client.delete(f"/api/videos/{video.id}")
    assert resp.status_code == 204, resp.text
    assert video.status == "deleted"
    assert ("delete_video", video.id) in queue.enqueued


def test_delete_video_staff_allowed() -> None:
    owner = _user()
    mod = _user(email="m@example.com", channel_name="mod", role="moderator")
    video = _video(owner.id)
    client = _client(FakeVideoRepo([video]), FakeQueue(), FakeAclRepo(), user=mod)
    assert client.delete(f"/api/videos/{video.id}").status_code == 204
    assert video.status == "deleted"


def test_delete_video_other_user_403() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    video = _video(owner.id)
    queue = FakeQueue()
    client = _client(FakeVideoRepo([video]), queue, FakeAclRepo(), user=other)
    assert client.delete(f"/api/videos/{video.id}").status_code == 403
    assert video.status == "ready"
    assert not queue.enqueued


def test_delete_video_unknown_and_deleted_404() -> None:
    owner = _user()
    video = _video(owner.id, status="deleted")
    client = _client(FakeVideoRepo([video]), FakeQueue(), FakeAclRepo(), user=owner)
    assert client.delete(f"/api/videos/{uuid.uuid4()}").status_code == 404
    assert client.delete(f"/api/videos/{video.id}").status_code == 404


def test_delete_video_guest_401() -> None:
    owner = _user()
    video = _video(owner.id)
    client = _client(FakeVideoRepo([video]), FakeQueue(), FakeAclRepo(), user=None)
    assert client.delete(f"/api/videos/{video.id}").status_code == 401


# --- GET /api/me/videos --------------------------------------------------------


def test_my_videos_lists_own_incl_private_and_failure_reason() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    vids = [
        _video(owner.id, title="pub"),
        _video(owner.id, title="priv", is_private=True),
        _video(owner.id, title="failed", status="failed", failure_reason="ffmpeg exit 1"),
        _video(owner.id, title="gone", status="deleted"),
        _video(other.id, title="not-mine"),
    ]
    client = _client(FakeVideoRepo(vids), FakeQueue(), FakeAclRepo(), user=owner)
    resp = client.get("/api/me/videos")
    assert resp.status_code == 200, resp.text
    items = {i["title"]: i for i in resp.json()["items"]}
    assert set(items) == {"pub", "priv", "failed"}
    assert items["failed"]["failure_reason"] == "ffmpeg exit 1"
    assert items["priv"]["is_private"] is True


def test_my_videos_guest_401() -> None:
    client = _client(FakeVideoRepo(), FakeQueue(), FakeAclRepo(), user=None)
    assert client.get("/api/me/videos").status_code == 401


# --- ACL ----------------------------------------------------------------------


def test_acl_list_owner_only() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    video = _video(owner.id, is_private=True)
    acl = FakeAclRepo()
    acl.entries[video.id] = {other.id: other}
    client = _client(FakeVideoRepo([video]), FakeQueue(), acl, user=owner)
    resp = client.get(f"/api/videos/{video.id}/acl")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [i["user_id"] for i in items] == [str(other.id)]
    assert items[0]["email"] == "o@example.com"

    client_other = _client(FakeVideoRepo([video]), FakeQueue(), acl, user=other)
    assert client_other.get(f"/api/videos/{video.id}/acl").status_code == 403

    client_guest = _client(FakeVideoRepo([video]), FakeQueue(), acl, user=None)
    assert client_guest.get(f"/api/videos/{video.id}/acl").status_code == 401


def test_acl_add_by_email_idempotent() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    video = _video(owner.id, is_private=True)
    acl = FakeAclRepo()
    client = _client(
        FakeVideoRepo([video]), FakeQueue(), acl,
        user_repo=FakeUserRepo([other]), user=owner,
    )
    r1 = client.post(f"/api/videos/{video.id}/acl", json={"email": "o@example.com"})
    r2 = client.post(f"/api/videos/{video.id}/acl", json={"email": "o@example.com"})
    assert r1.status_code == 204, r1.text
    assert r2.status_code == 204
    assert list(acl.entries[video.id]) == [other.id]


def test_acl_add_unknown_email_404_and_self_422() -> None:
    owner = _user()
    video = _video(owner.id, is_private=True)
    client = _client(
        FakeVideoRepo([video]), FakeQueue(), FakeAclRepo(),
        user_repo=FakeUserRepo([owner]), user=owner,
    )
    assert client.post(
        f"/api/videos/{video.id}/acl", json={"email": "nobody@example.com"}
    ).status_code == 404
    assert client.post(
        f"/api/videos/{video.id}/acl", json={"email": owner.email}
    ).status_code == 422


def test_acl_add_non_owner_403_and_guest_401() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    video = _video(owner.id, is_private=True)
    client = _client(
        FakeVideoRepo([video]), FakeQueue(), FakeAclRepo(),
        user_repo=FakeUserRepo([owner, other]), user=other,
    )
    assert client.post(
        f"/api/videos/{video.id}/acl", json={"email": "o@example.com"}
    ).status_code == 403
    client_guest = _client(
        FakeVideoRepo([video]), FakeQueue(), FakeAclRepo(),
        user_repo=FakeUserRepo([owner]), user=None,
    )
    assert client_guest.post(
        f"/api/videos/{video.id}/acl", json={"email": "o@example.com"}
    ).status_code == 401


def test_acl_remove_idempotent() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    video = _video(owner.id, is_private=True)
    acl = FakeAclRepo()
    acl.entries[video.id] = {other.id: other}
    client = _client(FakeVideoRepo([video]), FakeQueue(), acl, user=owner)
    assert client.delete(f"/api/videos/{video.id}/acl/{other.id}").status_code == 204
    assert client.delete(f"/api/videos/{video.id}/acl/{other.id}").status_code == 204
    assert not acl.entries[video.id]


def test_acl_remove_non_owner_403() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    video = _video(owner.id, is_private=True)
    client = _client(FakeVideoRepo([video]), FakeQueue(), FakeAclRepo(), user=other)
    assert client.delete(f"/api/videos/{video.id}/acl/{other.id}").status_code == 403


# --- worker wiring --------------------------------------------------------------


def test_delete_video_task_enqueued_and_registered() -> None:
    from worker import main as worker_main

    from app.worker import queue as queue_mod

    enqueued = set(re.findall(r'enqueue_job\(\s*"([a-z_]+)"', inspect.getsource(queue_mod)))
    registered = {f.__name__ for f in worker_main.WorkerSettings.functions}
    assert "delete_video" in enqueued, "queue must enqueue delete_video"
    assert "delete_video" in registered, "worker must register delete_video"
