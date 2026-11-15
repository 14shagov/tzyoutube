"""F5 red tests: channel page (+tabs videos/playlists, owner edit form, subscribe),
/subscriptions page (auth, channels + feed), /playlist/{id} page (visibility,
ordered items). Red until pages.py routes + templates land."""

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from test_channels_api import FakeChannelRepo, _user
from test_playlists_api import FakePlaylistRepo, _playlist
from test_subscriptions_api import FakeSubRepo
from test_viewing_api import _video

from app.api import channels as channels_api
from app.api import playlists as playlists_api
from app.api import videos as videos_api
from app.api.auth import get_current_user
from app.main import create_app
from app.services.fakes import FakeUserRepo


class _FeedRepo:
    def __init__(self, videos):
        self.videos = videos

    async def public_feed(self, *, limit, offset):
        return self.videos[offset : offset + limit]

    async def search(self, q, *, limit, offset):
        return [v for v in self.videos if q.lower() in v.title.lower()]


def _mk_video(owner_id, title="v", status="ready", is_private=False):
    v = _video(user_id=owner_id, title=title, status=status, is_private=is_private)
    if getattr(v, "created_at", None) is None:
        v.created_at = datetime.now(UTC)
    return v


def _app5(channel_repo, sub_repo, playlist_repo=None, user_repo=None, user=None, feed=()):
    app = create_app()
    app.dependency_overrides[channels_api.get_channel_repo] = lambda: channel_repo
    app.dependency_overrides[channels_api.get_subscription_repo] = lambda: sub_repo
    if playlist_repo is not None:
        app.dependency_overrides[playlists_api.get_playlist_repo] = lambda: playlist_repo
    if user_repo is not None:
        app.dependency_overrides[videos_api.get_video_repo] = lambda: user_repo
    app.dependency_overrides[videos_api.get_feed_repo] = lambda: _FeedRepo(list(feed))
    app.dependency_overrides[videos_api._optional_user] = lambda: user
    if user_repo is not None and hasattr(user_repo, "get_by_id"):
        from app.api import auth as auth_api

        app.dependency_overrides[auth_api.get_user_repo] = lambda: user_repo
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_channel_page_guest_200_with_header_tabs_and_cards() -> None:
    owner = _user(channel_name="CoolChan", channel_description="мой канал")
    v = _mk_video(owner.id, title="Hello world")
    chan = FakeChannelRepo({owner.id: owner}, counts={("sub", owner.id): 5, ("vid", owner.id): 1},
                           videos={owner.id: [v]})
    c = _app5(chan, FakeSubRepo(), user_repo=FakeUserRepo())
    r = c.get(f"/channel/{owner.id}")
    assert r.status_code == 200, r.text
    body = r.text
    assert "CoolChan" in body
    assert "мой канал" in body
    assert 'data-tab="videos"' in body and 'data-tab="playlists"' in body
    assert "Hello world" in body
    assert "/watch/" + str(v.id) in body
    assert "5" in body and "подписчик" in body
    assert "/static/channel.js" in body
    # guest: no subscribe button, no edit form
    assert "subscribe-btn" not in body
    assert "channel-edit-form" not in body


def test_channel_page_owner_sees_private_and_edit_form() -> None:
    owner = _user()
    pub = _mk_video(owner.id, title="public one")
    priv = _mk_video(owner.id, title="secret one", is_private=True)
    chan = FakeChannelRepo({owner.id: owner}, videos={owner.id: [pub, priv]})
    sub = FakeSubRepo()
    c = _app5(chan, sub, user_repo=FakeUserRepo(), user=owner)
    r = c.get(f"/channel/{owner.id}")
    assert r.status_code == 200
    body = r.text
    assert "secret one" in body  # owner sees private
    assert 'id="channel-edit-form"' in body
    assert "data-is-owner=\"1\"" in body
    assert "subscribe-btn" not in body  # cannot subscribe to self


def test_channel_page_stranger_does_not_see_private() -> None:
    owner = _user()
    stranger = _user(email="s@example.com", channel_name="stranger")
    pub = _mk_video(owner.id, title="public one")
    priv = _mk_video(owner.id, title="secret one", is_private=True)
    chan = FakeChannelRepo({owner.id: owner}, videos={owner.id: [pub, priv]})
    c = _app5(chan, FakeSubRepo(), user_repo=FakeUserRepo(), user=stranger)
    body = c.get(f"/channel/{owner.id}").text
    assert "public one" in body
    assert "secret one" not in body
    assert "subscribe-btn" in body  # stranger can subscribe
    assert 'data-subscribed="0"' in body


def test_channel_page_unknown_404() -> None:
    c = _app5(FakeChannelRepo({}), FakeSubRepo(), user_repo=FakeUserRepo())
    r = c.get(f"/channel/{uuid.uuid4()}")
    assert r.status_code == 404


def test_channel_page_xss_escaped() -> None:
    owner = _user(channel_name="<img src=x onerror=alert(1)>")
    chan = FakeChannelRepo({owner.id: owner}, videos={owner.id: []})
    body = _app5(chan, FakeSubRepo(), user_repo=FakeUserRepo()).get(f"/channel/{owner.id}").text
    assert "<img src=x" not in body
    assert "&lt;img" in body


def test_channel_playlists_tab_visibility() -> None:
    owner = _user()
    stranger = _user(email="s@example.com", channel_name="stranger")
    pub_pl = _playlist(owner.id, title="Public PL")
    priv_pl = _playlist(owner.id, title="Private PL", is_private=True)
    pl_repo = FakePlaylistRepo([pub_pl, priv_pl])
    chan = FakeChannelRepo({owner.id: owner}, videos={owner.id: []})
    # stranger: only public playlist
    body = _app5(chan, FakeSubRepo(), playlist_repo=pl_repo,
                 user_repo=FakeUserRepo(), user=stranger).get(
        f"/channel/{owner.id}?tab=playlists").text
    assert "Public PL" in body and "Private PL" not in body
    assert f"/playlist/{pub_pl.id}" in body
    # owner: both
    body = _app5(chan, FakeSubRepo(), playlist_repo=pl_repo,
                 user_repo=FakeUserRepo(), user=owner).get(
        f"/channel/{owner.id}?tab=playlists").text
    assert "Public PL" in body and "Private PL" in body


def test_subscriptions_page_guest_redirect_and_user_list() -> None:
    owner = _user(channel_name="SubChan")
    viewer = _user(email="v@example.com", channel_name="viewer")
    sub = FakeSubRepo()
    sub.subs.add((viewer.id, owner.id))
    sub.channels[owner.id] = owner
    v = _mk_video(owner.id, title="fresh video")
    sub.feed_videos = [v]
    # guest -> 303 to /login?next=/subscriptions
    r = _app5(FakeChannelRepo({}), sub, user_repo=FakeUserRepo()).get("/subscriptions")
    assert r.status_code == 303
    assert "/login?next=/subscriptions" in r.headers["location"]
    # user -> 200 with channel + feed video
    c = _app5(FakeChannelRepo({owner.id: owner}), sub, user_repo=FakeUserRepo(), user=viewer)
    body = c.get("/subscriptions").text
    assert "SubChan" in body
    assert f"/channel/{owner.id}" in body
    assert "fresh video" in body
    # empty state when no subs
    sub2 = FakeSubRepo()
    body = _app5(FakeChannelRepo({}), sub2, user_repo=FakeUserRepo(), user=viewer).get("/subscriptions").text
    assert 'data-testid="empty"' in body


def test_playlist_page_public_guest_and_owner_private() -> None:
    owner = _user()
    stranger = _user(email="s@example.com", channel_name="stranger")
    v1 = _mk_video(owner.id, title="first vid")
    v2 = _mk_video(owner.id, title="second vid")
    pl = _playlist(owner.id, title="My PL")
    pl_repo = FakePlaylistRepo([pl])
    pl_repo._videos = {v1.id: v1, v2.id: v2}
    from app.models import PlaylistItem

    pl_repo.items[pl.id] = {
        v2.id: PlaylistItem(playlist_id=pl.id, video_id=v2.id, position=2),
        v1.id: PlaylistItem(playlist_id=pl.id, video_id=v1.id, position=1),
    }
    # guest sees public playlist, items ordered by position
    c = _app5(FakeChannelRepo({owner.id: owner}), FakeSubRepo(), playlist_repo=pl_repo,
              user_repo=FakeUserRepo())
    body = c.get(f"/playlist/{pl.id}").text
    assert "My PL" in body
    assert body.index("first vid") < body.index("second vid")
    assert f"/watch/{v1.id}" in body
    assert "/static/playlist.js" not in body  # guest: no owner JS actions needed
    # private playlist: stranger 404, owner 200
    priv_pl = _playlist(owner.id, title="Hidden PL", is_private=True)
    pl_repo2 = FakePlaylistRepo([priv_pl])
    pl_repo2._videos = {}
    r = _app5(FakeChannelRepo({owner.id: owner}), FakeSubRepo(), playlist_repo=pl_repo2,
              user_repo=FakeUserRepo(), user=stranger).get(f"/playlist/{priv_pl.id}")
    assert r.status_code == 404
    r = _app5(FakeChannelRepo({owner.id: owner}), FakeSubRepo(), playlist_repo=pl_repo2,
              user_repo=FakeUserRepo(), user=owner).get(f"/playlist/{priv_pl.id}")
    assert r.status_code == 200 and "Hidden PL" in r.text
    # unknown playlist -> 404
    r = _app5(FakeChannelRepo({}), FakeSubRepo(), playlist_repo=FakePlaylistRepo(),
              user_repo=FakeUserRepo()).get(f"/playlist/{uuid.uuid4()}")
    assert r.status_code == 404
