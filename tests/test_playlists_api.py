"""3D red tests: playlists (FR-3) — CRUD, ordered items, visibility rules.

Public playlist: anyone sees it; items filtered to videos the viewer may see.
Private playlist: owner only (404 for everyone else, incl. guests).
Add item: owner only, video must exist/not deleted/be viewable by owner,
duplicate -> 409, position defaults to max+1.
"""

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from test_channels_api import FakeChannelRepo, _user
from test_video_manage_api import FakeAclRepo, FakeVideoRepo, _video

from app.api import channels as channels_api
from app.api import playlists as playlists_api
from app.api import videos as videos_api
from app.api.auth import get_current_user
from app.main import create_app
from app.models import Playlist, PlaylistItem
from app.services.playlists import DuplicateItem


class FakePlaylistRepo:
    def __init__(self, playlists=()):
        self.playlists = {p.id: p for p in playlists}
        self.items: dict[uuid.UUID, dict[uuid.UUID, PlaylistItem]] = {p.id: {} for p in playlists}

    async def create(self, playlist):
        if playlist.created_at is None:
            playlist.created_at = datetime.now(UTC)
        self.playlists[playlist.id] = playlist
        self.items[playlist.id] = {}
        return playlist

    async def get(self, playlist_id):
        return self.playlists.get(playlist_id)

    async def update(self, playlist):
        self.playlists[playlist.id] = playlist
        return playlist

    async def delete(self, playlist_id):
        self.playlists.pop(playlist_id, None)
        self.items.pop(playlist_id, None)

    async def list_channel_playlists(self, channel_id, *, viewer_id, limit, offset):
        pls = [p for p in self.playlists.values() if p.user_id == channel_id]
        if viewer_id is None or any(True for _ in ()):  # visibility filter below
            pass
        visible = [
            p for p in pls
            if not p.is_private or viewer_id == p.user_id
        ]
        return visible[offset : offset + limit]

    async def add_item(self, playlist_id, video_id, position):
        entries = self.items.setdefault(playlist_id, {})
        if video_id in entries:
            raise DuplicateItem("video already in playlist")
        item = PlaylistItem(playlist_id=playlist_id, video_id=video_id, position=position)
        entries[video_id] = item
        return item

    async def remove_item(self, playlist_id, video_id):
        self.items.get(playlist_id, {}).pop(video_id, None)

    async def list_items_with_videos(self, playlist_id):
        pairs = []
        for item in sorted(self.items.get(playlist_id, {}).values(), key=lambda i: i.position):
            pairs.append((item, self._videos[item.video_id]))
        return pairs

    async def max_position(self, playlist_id):
        return max((i.position for i in self.items.get(playlist_id, {}).values()), default=0)

    async def item_count(self, playlist_id):
        return len(self.items.get(playlist_id, {}))


def _playlist(owner_id, title="pl", is_private=False) -> Playlist:
    return Playlist(
        id=uuid.uuid4(), user_id=owner_id, title=title, is_private=is_private,
        created_at=datetime.now(UTC),
    )


def _client(playlist_repo, video_repo=None, user=None, channel_repo=None):
    app = create_app()
    app.dependency_overrides[playlists_api.get_playlist_repo] = lambda: playlist_repo
    app.dependency_overrides[videos_api.get_video_repo] = lambda: video_repo or FakeVideoRepo()
    app.dependency_overrides[videos_api.get_acl] = lambda: FakeAclRepo()
    app.dependency_overrides[channels_api.get_channel_repo] = lambda: (
        channel_repo or FakeChannelRepo({user.id: user} if user else {})
    )
    # guest-tolerant user for reads: always override _optional_user
    app.dependency_overrides[videos_api._optional_user] = lambda: user
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _repo_with(playlist, videos=()) -> FakePlaylistRepo:
    repo = FakePlaylistRepo([playlist])
    repo._videos = {v.id: v for v in videos}
    for v in videos:
        repo.items[playlist.id][v.id] = PlaylistItem(
            playlist_id=playlist.id, video_id=v.id, position=len(repo.items[playlist.id]) + 1
        )
    return repo


# --- create -------------------------------------------------------------------


def test_create_playlist_201() -> None:
    owner = _user()
    client = _client(FakePlaylistRepo(), user=owner)
    resp = client.post("/api/playlists", json={"title": "Favs", "is_private": True})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Favs" and body["is_private"] is True and body["item_count"] == 0


def test_create_playlist_guest_401_and_bad_title_422() -> None:
    owner = _user()
    guest = _client(FakePlaylistRepo(), user=None)
    assert guest.post("/api/playlists", json={"title": "x"}).status_code == 401
    client = _client(FakePlaylistRepo(), user=owner)
    assert client.post("/api/playlists", json={"title": ""}).status_code == 422
    assert client.post("/api/playlists", json={"title": "x" * 201}).status_code == 422


# --- read + visibility ----------------------------------------------------------


def test_get_public_playlist_guest_sees_only_public_videos() -> None:
    owner = _user()
    v_pub = _video(owner.id, title="pub")
    v_priv = _video(owner.id, title="priv", is_private=True)
    pl = _playlist(owner.id)
    client = _client(_repo_with(pl, [v_pub, v_priv]), user=None)
    resp = client.get(f"/api/playlists/{pl.id}")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [i["title"] for i in items] == ["pub"]
    assert items[0]["position"] == 1


def test_get_private_playlist_owner_only() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    pl = _playlist(owner.id, is_private=True)
    repo = _repo_with(pl)
    assert _client(repo, user=owner).get(f"/api/playlists/{pl.id}").status_code == 200
    assert _client(repo, user=other).get(f"/api/playlists/{pl.id}").status_code == 404
    assert _client(repo, user=None).get(f"/api/playlists/{pl.id}").status_code == 404


def test_get_unknown_playlist_404() -> None:
    owner = _user()
    client = _client(FakePlaylistRepo(), user=owner)
    assert client.get(f"/api/playlists/{uuid.uuid4()}").status_code == 404


# --- patch / delete --------------------------------------------------------------


def test_patch_playlist_owner_only() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    pl = _playlist(owner.id)
    repo = FakePlaylistRepo([pl])
    client = _client(repo, user=owner)
    resp = client.patch(f"/api/playlists/{pl.id}", json={"title": "New", "is_private": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "New" and resp.json()["is_private"] is True
    assert _client(repo, user=other).patch(
        f"/api/playlists/{pl.id}", json={"title": "hax"}
    ).status_code == 403
    assert _client(repo, user=None).patch(
        f"/api/playlists/{pl.id}", json={"title": "hax"}
    ).status_code == 401


def test_delete_playlist_owner_only() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    pl = _playlist(owner.id)
    repo = FakePlaylistRepo([pl])
    assert _client(repo, user=other).delete(f"/api/playlists/{pl.id}").status_code == 403
    assert _client(repo, user=None).delete(f"/api/playlists/{pl.id}").status_code == 401
    assert _client(repo, user=owner).delete(f"/api/playlists/{pl.id}").status_code == 204
    assert pl.id not in repo.playlists


# --- items ----------------------------------------------------------------------


def test_add_item_positions_and_duplicate_409() -> None:
    owner = _user()
    v1, v2 = _video(owner.id, "a"), _video(owner.id, "b")
    pl = _playlist(owner.id)
    video_repo = FakeVideoRepo([v1, v2])
    repo = FakePlaylistRepo([pl])
    client = _client(repo, video_repo=video_repo, user=owner)
    r1 = client.post(f"/api/playlists/{pl.id}/items", json={"video_id": str(v1.id)})
    r2 = client.post(f"/api/playlists/{pl.id}/items", json={"video_id": str(v2.id)})
    assert r1.status_code == 201 and r1.json()["position"] == 1, r1.text
    assert r2.status_code == 201 and r2.json()["position"] == 2, r2.text
    dup = client.post(f"/api/playlists/{pl.id}/items", json={"video_id": str(v1.id)})
    assert dup.status_code == 409
    explicit = client.post(
        f"/api/playlists/{pl.id}/items", json={"video_id": str(v2.id), "position": 0}
    )
    assert explicit.status_code == 409  # still a duplicate


def test_add_item_validation() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    v_pub = _video(owner.id)
    v_priv_other = _video(other.id, is_private=True)
    v_deleted = _video(owner.id, status="deleted")
    pl = _playlist(owner.id)
    video_repo = FakeVideoRepo([v_pub, v_priv_other, v_deleted])
    repo = FakePlaylistRepo([pl])
    client = _client(repo, video_repo=video_repo, user=owner)
    assert client.post(
        f"/api/playlists/{pl.id}/items", json={"video_id": str(uuid.uuid4())}
    ).status_code == 404  # unknown video
    assert client.post(
        f"/api/playlists/{pl.id}/items", json={"video_id": str(v_deleted.id)}
    ).status_code == 404  # deleted video
    assert client.post(
        f"/api/playlists/{pl.id}/items", json={"video_id": str(v_priv_other.id)}
    ).status_code == 404  # not viewable by owner
    other_client = _client(repo, video_repo=video_repo, user=other)
    assert other_client.post(
        f"/api/playlists/{pl.id}/items", json={"video_id": str(v_pub.id)}
    ).status_code == 403  # not the playlist owner
    guest = _client(repo, video_repo=video_repo, user=None)
    assert guest.post(
        f"/api/playlists/{pl.id}/items", json={"video_id": str(v_pub.id)}
    ).status_code == 401


def test_remove_item_idempotent_owner_only() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    v = _video(owner.id)
    pl = _playlist(owner.id)
    repo = _repo_with(pl, [v])
    client = _client(repo, user=owner)
    assert client.delete(f"/api/playlists/{pl.id}/items/{v.id}").status_code == 204
    assert client.delete(f"/api/playlists/{pl.id}/items/{v.id}").status_code == 204
    assert _client(repo, user=other).delete(f"/api/playlists/{pl.id}/items/{v.id}").status_code == 403


# --- channel playlists ------------------------------------------------------------


def test_channel_playlists_visibility() -> None:
    owner = _user()
    pub_pl = _playlist(owner.id, title="pub")
    priv_pl = _playlist(owner.id, title="priv", is_private=True)
    repo = FakePlaylistRepo([pub_pl, priv_pl])
    channels = FakeChannelRepo({owner.id: owner})
    # guest: only public
    resp = _client(repo, user=None, channel_repo=channels).get(
        f"/api/channels/{owner.id}/playlists"
    )
    assert resp.status_code == 200, resp.text
    assert [i["title"] for i in resp.json()["items"]] == ["pub"]
    # owner: both
    resp = _client(repo, user=owner, channel_repo=channels).get(
        f"/api/channels/{owner.id}/playlists"
    )
    assert sorted(i["title"] for i in resp.json()["items"]) == ["priv", "pub"]
    # unknown channel 404
    assert _client(repo, user=None, channel_repo=FakeChannelRepo({})).get(
        f"/api/channels/{uuid.uuid4()}/playlists"
    ).status_code == 404
