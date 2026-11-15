"""3A red tests: channel profile API (FR-3).

GET /api/channels/{id}            — name, description, avatar stub, counts, subscribed
GET /api/channels/{id}/videos     — paginated; public for guests/others, full for owner
PATCH /api/channels/{id}          — owner only; name/description; 409 on taken name

Fakes implement the repo protocols the endpoints will consume; the API
modules themselves land in the fix commit, so these tests fail (red)
until then.
"""

import uuid

from fastapi.testclient import TestClient

from app.api import videos as videos_api
from app.api.auth import get_current_user
from app.main import create_app
from app.models import User, Video


def _user(**kw) -> User:
    base = {
        "id": uuid.uuid4(),
        "email": "u@example.com",
        "password_hash": "x",
        "channel_name": "chan",
        "role": "user",
    }
    base.update(kw)
    return User(**base)


def _video(owner_id, title="v", status="ready", is_private=False) -> Video:
    return Video(
        id=uuid.uuid4(),
        user_id=owner_id,
        title=title,
        description="",
        is_private=is_private,
        status=status,
        raw_key="raw/x",
        duration_seconds=5,
        view_count=3,
    )


class FakeChannelRepo:
    def __init__(self, users, counts=None, videos=None, taken_names=()):
        self.users = users
        self.counts = counts or {}
        self.videos = videos or {}
        self.taken_names = set(taken_names)
        self.last_viewer_id = "unset"

    async def get_user(self, user_id):
        return self.users.get(user_id)

    async def subscriber_count(self, channel_id):
        return self.counts.get(("sub", channel_id), 0)

    async def public_video_count(self, channel_id):
        return self.counts.get(("vid", channel_id), 0)

    async def list_channel_videos(self, channel_id, *, viewer_id, limit, offset):
        self.last_viewer_id = viewer_id
        return self.videos.get(channel_id, [])[offset : offset + limit]

    async def channel_name_taken(self, name, *, exclude_user_id):
        return name in self.taken_names

    async def update_channel(self, user, *, channel_name, description):
        if channel_name is not None:
            user.channel_name = channel_name
        if description is not None:
            user.channel_description = description
        return user


class FakeSubscriptionRepo:
    def __init__(self, subs=()):
        self.subs = set(subs)

    async def is_subscribed(self, subscriber_id, channel_id):
        return (subscriber_id, channel_id) in self.subs


def _client(channel_repo, sub_repo, user=None):
    """TestClient with channel/subscription repos and the current user faked."""
    from app.api import channels as channels_api

    app = create_app()
    app.dependency_overrides[channels_api.get_channel_repo] = lambda: channel_repo
    app.dependency_overrides[channels_api.get_subscription_repo] = lambda: sub_repo
    app.dependency_overrides[videos_api._optional_user] = lambda: user
    if user is not None:
        # Guest case: leave the real get_current_user in place so it 401s.
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_get_channel_public_fields() -> None:
    owner = _user(channel_name="CoolChannel")
    owner.channel_description = "my desc"
    repo = FakeChannelRepo(
        {owner.id: owner},
        counts={("sub", owner.id): 7, ("vid", owner.id): 3},
    )
    sub = FakeSubscriptionRepo()
    client = _client(repo, sub, user=None)
    resp = client.get(f"/api/channels/{owner.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(owner.id)
    assert body["channel_name"] == "CoolChannel"
    assert body["description"] == "my desc"
    assert body["avatar_url"]  # stub, non-empty
    assert body["subscriber_count"] == 7
    assert body["public_video_count"] == 3
    assert body["subscribed"] is False


def test_get_channel_subscribed_state_for_viewer() -> None:
    owner = _user()
    viewer = _user(email="v@example.com", channel_name="viewer")
    sub = FakeSubscriptionRepo({(viewer.id, owner.id)})
    client = _client(FakeChannelRepo({owner.id: owner}), sub, user=viewer)
    resp = client.get(f"/api/channels/{owner.id}")
    assert resp.status_code == 200
    assert resp.json()["subscribed"] is True


def test_get_channel_unknown_404() -> None:
    client = _client(FakeChannelRepo({}), FakeSubscriptionRepo(), user=None)
    assert client.get(f"/api/channels/{uuid.uuid4()}").status_code == 404


def test_channel_videos_paginated_and_viewer_passed() -> None:
    owner = _user()
    vids = [_video(owner.id, title=f"v{i}") for i in range(5)]
    repo = FakeChannelRepo({owner.id: owner}, videos={owner.id: vids})
    client = _client(repo, FakeSubscriptionRepo(), user=None)
    resp = client.get(f"/api/channels/{owner.id}/videos", params={"limit": 2, "offset": 1})
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [i["title"] for i in items] == ["v1", "v2"]
    assert repo.last_viewer_id is None  # guest
    assert all("status" in i and "is_private" in i for i in items)


def test_channel_videos_owner_sees_viewer_id() -> None:
    owner = _user()
    repo = FakeChannelRepo({owner.id: owner}, videos={owner.id: [_video(owner.id)]})
    client = _client(repo, FakeSubscriptionRepo(), user=owner)
    resp = client.get(f"/api/channels/{owner.id}/videos")
    assert resp.status_code == 200
    assert repo.last_viewer_id == owner.id


def test_channel_videos_unknown_channel_404() -> None:
    client = _client(FakeChannelRepo({}), FakeSubscriptionRepo(), user=None)
    assert client.get(f"/api/channels/{uuid.uuid4()}/videos").status_code == 404


def test_patch_own_channel_ok() -> None:
    owner = _user()
    repo = FakeChannelRepo({owner.id: owner})
    client = _client(repo, FakeSubscriptionRepo(), user=owner)
    resp = client.patch(
        f"/api/channels/{owner.id}",
        json={"channel_name": "NewName", "description": "new"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["channel_name"] == "NewName"
    assert body["description"] == "new"


def test_patch_other_channel_403() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    repo = FakeChannelRepo({owner.id: owner, other.id: other})
    client = _client(repo, FakeSubscriptionRepo(), user=other)
    resp = client.patch(f"/api/channels/{owner.id}", json={"description": "hax"})
    assert resp.status_code == 403


def test_patch_channel_name_taken_409() -> None:
    owner = _user()
    repo = FakeChannelRepo({owner.id: owner}, taken_names={"TakenName"})
    client = _client(repo, FakeSubscriptionRepo(), user=owner)
    resp = client.patch(f"/api/channels/{owner.id}", json={"channel_name": "TakenName"})
    assert resp.status_code == 409


def test_patch_channel_guest_401() -> None:
    owner = _user()
    repo = FakeChannelRepo({owner.id: owner})
    client = _client(repo, FakeSubscriptionRepo(), user=None)
    resp = client.patch(f"/api/channels/{owner.id}", json={"description": "x"})
    assert resp.status_code == 401


def test_patch_channel_title_too_long_422() -> None:
    owner = _user()
    repo = FakeChannelRepo({owner.id: owner})
    client = _client(repo, FakeSubscriptionRepo(), user=owner)
    resp = client.patch(f"/api/channels/{owner.id}", json={"channel_name": "x" * 101})
    assert resp.status_code == 422
