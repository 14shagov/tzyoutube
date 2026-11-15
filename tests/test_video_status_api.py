"""3F red tests: GET /api/videos/{id}/status — processing/ready/failed + reason.

Access: ready+public -> anyone (guest included); any other state -> owner,
staff, or ACL member only; unknown/deleted -> 404 for everyone.
"""

import uuid

from fastapi.testclient import TestClient
from test_channels_api import _user
from test_video_manage_api import FakeAclRepo, FakeVideoRepo, _video

from app.api import videos as videos_api
from app.main import create_app


def _client(video_repo, acl=None, optional=None):
    app = create_app()
    app.dependency_overrides[videos_api.get_video_repo] = lambda: video_repo
    app.dependency_overrides[videos_api.get_acl] = lambda: acl or FakeAclRepo()
    holder = {"user": optional}
    app.dependency_overrides[videos_api._optional_user] = lambda: holder["user"]
    return TestClient(app), holder


def test_owner_polls_processing_status() -> None:
    owner = _user()
    video = _video(owner.id, status="processing")
    client, _ = _client(FakeVideoRepo([video]), optional=owner)
    r = client.get(f"/api/videos/{video.id}/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == str(video.id)
    assert body["status"] == "processing"
    assert body["failure_reason"] is None


def test_failed_status_includes_reason() -> None:
    owner = _user()
    video = _video(owner.id, status="failed", failure_reason="ffmpeg: moov atom not found")
    client, _ = _client(FakeVideoRepo([video]), optional=owner)
    r = client.get(f"/api/videos/{video.id}/status")
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    assert "moov atom" in r.json()["failure_reason"]


def test_guest_can_read_status_of_public_ready_video() -> None:
    owner = _user()
    video = _video(owner.id, status="ready")
    client, _ = _client(FakeVideoRepo([video]), optional=None)
    r = client.get(f"/api/videos/{video.id}/status")
    assert r.status_code == 200 and r.json()["status"] == "ready"


def test_processing_status_hidden_from_strangers_and_guests() -> None:
    owner = _user()
    stranger = _user(email="s@example.com", channel_name="str")
    mod = _user(email="m@example.com", channel_name="mod", role="moderator")
    video = _video(owner.id, status="processing")
    repo = FakeVideoRepo([video])
    client, holder = _client(repo, optional=None)
    assert client.get(f"/api/videos/{video.id}/status").status_code == 404
    holder["user"] = stranger
    assert client.get(f"/api/videos/{video.id}/status").status_code == 404
    holder["user"] = owner
    assert client.get(f"/api/videos/{video.id}/status").status_code == 200
    holder["user"] = mod
    assert client.get(f"/api/videos/{video.id}/status").status_code == 200


def test_private_video_status_for_acl_member_but_not_stranger() -> None:
    owner = _user()
    member = _user(email="m@example.com", channel_name="mem")
    stranger = _user(email="s@example.com", channel_name="str")
    video = _video(owner.id, status="ready", is_private=True)
    acl = FakeAclRepo()
    acl.entries[video.id] = {member.id: True}
    repo = FakeVideoRepo([video])
    client, holder = _client(repo, acl=acl, optional=None)
    assert client.get(f"/api/videos/{video.id}/status").status_code == 404
    holder["user"] = stranger
    assert client.get(f"/api/videos/{video.id}/status").status_code == 404
    holder["user"] = member
    assert client.get(f"/api/videos/{video.id}/status").status_code == 200


def test_unknown_and_deleted_404_even_for_owner() -> None:
    owner = _user()
    deleted = _video(owner.id, status="deleted")
    repo = FakeVideoRepo([deleted])
    client, _ = _client(repo, optional=owner)
    assert client.get(f"/api/videos/{uuid.uuid4()}/status").status_code == 404
    assert client.get(f"/api/videos/{deleted.id}/status").status_code == 404
