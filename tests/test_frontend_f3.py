"""F3 (owner m02359): watch page + small API additions it needs.

API: ReactionOut.my_reaction + GET /api/videos/{id}/reactions;
CommentOut.user_id/channel_name (authors resolved via UserRepo.get_by_ids).
Page: /watch/{video_id} renders player (play_url), title, channel + subscribe,
like/dislike with my state, description, comments with authors, counters,
recommendations; XSS escaped everywhere; 404 for unknown/private/hidden.
Red before implementation.
"""

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from test_viewing_api import (
    FakeAclRepo,
    FakeCommentRepo,
    FakeFeedRepo,
    FakeReactionRepo,
    FakeStorage,
    FakeUser,
    FakeVideoRepo,
    FakeViewRepo,
    _video,
)

from app.api import auth as auth_api
from app.api import channels as channels_api
from app.api import videos as videos_api
from app.main import create_app
from app.services.fakes import FakeUserRepo


class FakeChannelRepo:
    def __init__(self) -> None:
        self.counts = {}

    async def get_user(self, user_id):
        return None

    async def subscriber_count(self, channel_id):
        return self.counts.get(channel_id, 0)

    async def public_video_count(self, channel_id):
        return 0


class FakeSubRepo:
    def __init__(self) -> None:
        self.subs = set()

    async def is_subscribed(self, subscriber_id, channel_id):
        return (subscriber_id, channel_id) in self.subs


def _app(videos, user=None, comments=None, reactions=None):
    app = create_app()
    video_repo = FakeVideoRepo(videos)
    feed_repo = FakeFeedRepo(videos)
    comment_repo = FakeCommentRepo()
    for c in comments or []:
        comment_repo.comments.append(c)
    reaction_repo = FakeReactionRepo()
    for (uid, vid), val in (reactions or {}).items():
        reaction_repo.reactions[(uid, vid)] = val
    user_repo = FakeUserRepo()
    app.dependency_overrides[videos_api.get_video_repo] = lambda: video_repo
    app.dependency_overrides[videos_api.get_feed_repo] = lambda: feed_repo
    app.dependency_overrides[videos_api.get_view_repo] = lambda: FakeViewRepo()
    app.dependency_overrides[videos_api.get_reaction_repo] = lambda: reaction_repo
    app.dependency_overrides[videos_api.get_comment_repo] = lambda: comment_repo
    app.dependency_overrides[videos_api.get_storage] = lambda: FakeStorage()
    app.dependency_overrides[videos_api.get_acl] = lambda: FakeAclRepo()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: user_repo
    app.dependency_overrides[channels_api.get_channel_repo] = lambda: FakeChannelRepo()
    app.dependency_overrides[channels_api.get_subscription_repo] = lambda: FakeSubRepo()
    if user is not None:
        app.dependency_overrides[videos_api._optional_user] = lambda: user
        app.dependency_overrides[auth_api.get_current_user] = lambda: user
    return TestClient(app), user_repo, reaction_repo


# --- API: my_reaction ---------------------------------------------------------


def test_set_reaction_response_includes_my_reaction() -> None:
    v = _video()
    user = FakeUser()
    c, _, _ = _app([v], user=user)
    r = c.post(f"/api/videos/{v.id}/reactions", json={"value": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["likes"] == 1 and body["dislikes"] == 0
    assert body["my_reaction"] == 1
    r = c.post(f"/api/videos/{v.id}/reactions", json={"value": 0})
    assert r.json()["my_reaction"] is None


def test_get_reactions_returns_counts_and_my_reaction() -> None:
    v = _video()
    user = FakeUser()
    c, _, _reaction_repo = _app([v], user=user, reactions={(user.id, v.id): -1})
    r = c.get(f"/api/videos/{v.id}/reactions")
    assert r.status_code == 200
    body = r.json()
    assert body == {"likes": 0, "dislikes": 1, "my_reaction": -1}


def test_get_reactions_guest_my_reaction_null() -> None:
    v = _video()
    c, _, _ = _app([v])
    r = c.get(f"/api/videos/{v.id}/reactions")
    assert r.status_code == 200
    assert r.json()["my_reaction"] is None


# --- API: comment authors -------------------------------------------------------


def test_add_comment_returns_author_fields() -> None:
    v = _video()
    user = FakeUser()
    user.channel_name = "Author"
    c, _, _ = _app([v], user=user)
    r = c.post(f"/api/videos/{v.id}/comments", json={"text": "hi"})
    assert r.status_code == 201
    body = r.json()
    assert body["user_id"] == str(user.id)
    assert body["channel_name"] == "Author"


def test_list_comments_include_authors() -> None:
    v = _video()
    user = FakeUser()
    user.channel_name = "Commenter"
    comment = SimpleNamespace(
        id=uuid.uuid4(), video_id=v.id, user_id=user.id, text="nice", parent_id=None,
    )
    c, user_repo, _ = _app([v], comments=[comment])
    u = FakeUser()
    u.id = user.id
    u.channel_name = "Commenter"
    user_repo.users[user.id] = u
    r = c.get(f"/api/videos/{v.id}/comments")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["channel_name"] == "Commenter"
    assert item["user_id"] == str(user.id)


# --- watch page -----------------------------------------------------------------


def test_watch_page_guest_200_full_layout() -> None:
    v = _video(title="Big Buck")
    other = _video(title="Recommendation")
    c, _, _ = _app([v, other])
    r = c.get(f"/watch/{v.id}")
    assert r.status_code == 200
    body = r.text
    assert "<video" in body and "controls" in body
    assert f'data-video-id="{v.id}"' in body
    assert "Big Buck" in body
    # like/dislike segmented control + counts + my state marker
    assert 'id="like-btn"' in body and 'id="dislike-btn"' in body
    assert 'data-my-reaction' in body
    # subscribe button + comments section + recommendations
    assert 'id="subscribe-btn"' in body
    assert 'id="comment-form"' in body or 'id="comments"' in body
    assert "Recommendation" in body  # right-column recs
    assert 'src="/static/watch.js"' in body


def test_watch_page_xss_escaped_in_title_desc_comment() -> None:
    v = _video(title="<script>alert(1)</script>", description="<img src=x onerror=1>")
    comment = SimpleNamespace(
        id=uuid.uuid4(), video_id=v.id, user_id=uuid.uuid4(),
        text="<script>alert(2)</script>", parent_id=None,
    )
    c, user_repo, _ = _app([v], comments=[comment])
    u = FakeUser()
    u.id = comment.user_id
    u.channel_name = "<b>bob</b>"
    user_repo.users[u.id] = u
    r = c.get(f"/watch/{v.id}")
    assert r.status_code == 200
    assert "<script>alert" not in r.text
    assert "<img src=x onerror=1>" not in r.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
    assert "&lt;b&gt;bob&lt;/b&gt;" in r.text


def test_watch_unknown_video_404() -> None:
    c, _, _ = _app([])
    assert c.get(f"/watch/{uuid.uuid4()}").status_code == 404


def test_watch_private_404_for_stranger_200_for_owner() -> None:
    v = _video(is_private=True)
    owner = FakeUser()
    owner.id = v.user_id
    c, _, _ = _app([v])
    assert c.get(f"/watch/{v.id}").status_code == 404
    c2, _, _ = _app([v], user=owner)
    r = c2.get(f"/watch/{v.id}")
    assert r.status_code == 200
    assert "<video" in r.text


def test_watch_hidden_visible_to_owner_not_stranger() -> None:
    v = _video(status="hidden")
    owner = FakeUser()
    owner.id = v.user_id
    c, _, _ = _app([v])
    assert c.get(f"/watch/{v.id}").status_code == 404
    c2, _, _ = _app([v], user=owner)
    assert c2.get(f"/watch/{v.id}").status_code == 200


def test_watch_page_shows_reaction_state_for_user() -> None:
    v = _video()
    user = FakeUser()
    c, _, _ = _app([v], user=user, reactions={(user.id, v.id): 1})
    r = c.get(f"/watch/{v.id}")
    assert 'data-my-reaction="1"' in r.text
