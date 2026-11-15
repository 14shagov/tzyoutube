"""3B red tests: subscriptions (FR-3).

POST/DELETE /api/channels/{id}/subscribe — idempotent, no self-subscribe
GET /api/subscriptions          — my subscribed channels (auth)
GET /api/feed/subscriptions     — videos of subscribed channels (auth)
"""

import uuid

from fastapi.testclient import TestClient

from app.api import videos as videos_api
from app.api.auth import get_current_user
from app.main import create_app
from test_channels_api import FakeChannelRepo, _user, _video


class FakeSubRepo:
    def __init__(self):
        self.subs: set[tuple[uuid.UUID, uuid.UUID]] = set()
        self.channels = {}
        self.feed_videos = []

    async def is_subscribed(self, subscriber_id, channel_id):
        return (subscriber_id, channel_id) in self.subs

    async def subscribe(self, subscriber_id, channel_id):
        self.subs.add((subscriber_id, channel_id))

    async def unsubscribe(self, subscriber_id, channel_id):
        self.subs.discard((subscriber_id, channel_id))

    async def subscribed_channels(self, subscriber_id, *, limit, offset):
        mine = [
            self.channels[c]
            for (s, c) in sorted(self.subs)
            if s == subscriber_id and c in self.channels
        ]
        return mine[offset : offset + limit]

    async def subscriptions_feed(self, subscriber_id, *, limit, offset):
        return self.feed_videos[offset : offset + limit]


def _client(channel_repo, sub_repo, user=None):
    from app.api import channels as channels_api

    app = create_app()
    app.dependency_overrides[channels_api.get_channel_repo] = lambda: channel_repo
    app.dependency_overrides[channels_api.get_subscription_repo] = lambda: sub_repo
    app.dependency_overrides[videos_api._optional_user] = lambda: user
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_subscribe_204_and_idempotent() -> None:
    owner = _user()
    viewer = _user(email="v@example.com", channel_name="viewer")
    sub = FakeSubRepo()
    client = _client(FakeChannelRepo({owner.id: owner}), sub, user=viewer)
    r1 = client.post(f"/api/channels/{owner.id}/subscribe")
    r2 = client.post(f"/api/channels/{owner.id}/subscribe")
    assert r1.status_code == 204, r1.text
    assert r2.status_code == 204, r2.text
    assert sub.subs == {(viewer.id, owner.id)}


def test_subscribe_self_422() -> None:
    owner = _user()
    sub = FakeSubRepo()
    client = _client(FakeChannelRepo({owner.id: owner}), sub, user=owner)
    resp = client.post(f"/api/channels/{owner.id}/subscribe")
    assert resp.status_code == 422
    assert not sub.subs


def test_subscribe_unknown_channel_404() -> None:
    viewer = _user(email="v@example.com", channel_name="viewer")
    client = _client(FakeChannelRepo({}), FakeSubRepo(), user=viewer)
    assert client.post(f"/api/channels/{uuid.uuid4()}/subscribe").status_code == 404


def test_subscribe_guest_401() -> None:
    owner = _user()
    client = _client(FakeChannelRepo({owner.id: owner}), FakeSubRepo(), user=None)
    assert client.post(f"/api/channels/{owner.id}/subscribe").status_code == 401


def test_unsubscribe_204_idempotent() -> None:
    owner = _user()
    viewer = _user(email="v@example.com", channel_name="viewer")
    sub = FakeSubRepo()
    sub.subs.add((viewer.id, owner.id))
    client = _client(FakeChannelRepo({owner.id: owner}), sub, user=viewer)
    assert client.delete(f"/api/channels/{owner.id}/subscribe").status_code == 204
    assert client.delete(f"/api/channels/{owner.id}/subscribe").status_code == 204
    assert not sub.subs


def test_unsubscribe_guest_401() -> None:
    owner = _user()
    client = _client(FakeChannelRepo({owner.id: owner}), FakeSubRepo(), user=None)
    assert client.delete(f"/api/channels/{owner.id}/subscribe").status_code == 401


def test_my_subscriptions_list() -> None:
    viewer = _user(email="v@example.com", channel_name="viewer")
    a, b = _user(email="a@example.com", channel_name="chanA"), _user(
        email="b@example.com", channel_name="chanB"
    )
    sub = FakeSubRepo()
    sub.channels = {a.id: a, b.id: b}
    sub.subs = {(viewer.id, a.id), (viewer.id, b.id)}
    client = _client(FakeChannelRepo({}), sub, user=viewer)
    resp = client.get("/api/subscriptions")
    assert resp.status_code == 200, resp.text
    names = {i["channel_name"] for i in resp.json()["items"]}
    assert names == {"chanA", "chanB"}
    assert all(i["avatar_url"] for i in resp.json()["items"])


def test_my_subscriptions_guest_401() -> None:
    client = _client(FakeChannelRepo({}), FakeSubRepo(), user=None)
    assert client.get("/api/subscriptions").status_code == 401


def test_subscriptions_feed() -> None:
    viewer = _user(email="v@example.com", channel_name="viewer")
    owner = _user()
    vids = [_video(owner.id, title=f"f{i}") for i in range(3)]
    sub = FakeSubRepo()
    sub.feed_videos = vids
    client = _client(FakeChannelRepo({}), sub, user=viewer)
    resp = client.get("/api/feed/subscriptions", params={"limit": 2})
    assert resp.status_code == 200, resp.text
    assert [i["title"] for i in resp.json()["items"]] == ["f0", "f1"]


def test_subscriptions_feed_guest_401() -> None:
    client = _client(FakeChannelRepo({}), FakeSubRepo(), user=None)
    assert client.get("/api/feed/subscriptions").status_code == 401
