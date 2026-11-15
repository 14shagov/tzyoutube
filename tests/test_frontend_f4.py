"""F4 (owner m02359): upload page (dropzone + XHR progress + status polling) and
"Мои видео" page (status badges + failure_reason, edit/delete/privacy, ACL).

Pages: GET /upload (guest → 303 /login?next=/upload), GET /me/videos (guest → 303).
Red before implementation.
"""

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from test_channels_api import _user
from test_video_manage_api import FakeQueue, FakeVideoRepo
from test_viewing_api import FakeStorage, _video

from app.api import auth as auth_api
from app.api import videos as videos_api
from app.main import create_app
from app.services.fakes import FakeUserRepo


class FakeAclRepo:
    def __init__(self) -> None:
        self.entries: dict[uuid.UUID, dict[uuid.UUID, object]] = {}

    async def is_allowed(self, video_id, user_id):
        return user_id in self.entries.get(video_id, {})

    async def add(self, video_id, user_id):
        self.entries.setdefault(video_id, {})[user_id] = True

    async def remove(self, video_id, user_id):
        self.entries.get(video_id, {}).pop(user_id, None)

    async def list_entries(self, video_id):
        return list(self.entries.get(video_id, {}).values())


def _app4(videos=(), user=None, acl=None):
    app = create_app()
    video_repo = FakeVideoRepo(list(videos))
    acl = acl or FakeAclRepo()
    queue = FakeQueue()
    user_repo = FakeUserRepo()
    if user is not None:
        user_repo.users[user.id] = user
        user_repo.by_email[user.email] = user.id
    app.dependency_overrides[videos_api.get_video_repo] = lambda: video_repo
    app.dependency_overrides[videos_api.get_queue] = lambda: queue
    app.dependency_overrides[videos_api.get_acl] = lambda: acl
    app.dependency_overrides[videos_api.get_storage] = lambda: FakeStorage()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: user_repo
    if user is not None:
        app.dependency_overrides[auth_api.get_current_user] = lambda: user
        app.dependency_overrides[videos_api._optional_user] = lambda: user
    return TestClient(app), video_repo, acl, user_repo


def _my_video(owner_id, title="v", status="ready", is_private=False, failure_reason=None):
    v = _video(user_id=owner_id, title=title)
    v.status = status
    v.is_private = is_private
    v.failure_reason = failure_reason
    v.created_at = datetime.now(UTC)
    return v


# --- /upload page ---------------------------------------------------------------


def test_upload_page_guest_redirects_to_login() -> None:
    c, _, _, _ = _app4()
    r = c.get("/upload", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")
    assert "next=/upload" in r.headers["location"]


def test_upload_page_user_200_dropzone_progress_xhr() -> None:
    user = _user()
    c, _, _, _ = _app4(user=user)
    r = c.get("/upload")
    assert r.status_code == 200
    body = r.text
    assert 'id="dropzone"' in body                      # drag&drop zone
    assert 'id="file-input"' in body and 'accept="video/' in body
    assert 'id="title-input"' in body and 'maxlength="200"' in body
    assert 'id="desc-input"' in body
    assert 'id="progress"' in body                      # upload progress bar
    assert 'data-max-size=' in body                     # server limit for JS
    assert 'src="/static/upload.js"' in body
    assert 'id="upload-error"' in body
    assert "onclick" not in body and "onsubmit" not in body


# --- /me/videos page ------------------------------------------------------------


def test_my_videos_guest_redirects_to_login() -> None:
    c, _, _, _ = _app4()
    r = c.get("/me/videos", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_my_videos_lists_own_with_status_and_failure() -> None:
    owner = _user()
    other = _user(email="o@example.com", channel_name="other")
    v1 = _my_video(owner.id, title="Готово", status="ready")
    v2 = _my_video(owner.id, title="В обработке", status="processing")
    v3 = _my_video(owner.id, title="Сбой", status="failed", failure_reason="ffmpeg killed after 600s")
    foreign = _my_video(other.id, title="Чужое")
    deleted = _my_video(owner.id, title="Удалено", status="deleted")
    c, _, _, _ = _app4([v1, v2, v3, foreign, deleted], user=owner)
    r = c.get("/me/videos")
    assert r.status_code == 200
    body = r.text
    assert "Готово" in body and "В обработке" in body and "Сбой" in body
    assert "Чужое" not in body and "Удалено" not in body
    assert "failed" in body and "ffmpeg killed after 600s" in body  # failure_reason shown
    assert f'data-video-id="{v1.id}"' in body
    assert f'href="/watch/{v1.id}"' in body
    assert 'src="/static/manage.js"' in body
    # edit / delete / privacy controls per row
    assert body.count("edit-btn") == 3
    assert body.count("del-btn") == 3
    assert 'name="is_private"' in body
    assert "onclick" not in body


def test_my_videos_private_shows_acl_section() -> None:
    owner = _user()
    member = _user(email="m@example.com", channel_name="member")
    v = _my_video(owner.id, title="Приватное", is_private=True)
    acl = FakeAclRepo()
    acl.entries[v.id] = {member.id: member}
    c, _, _, _ = _app4([v], user=owner, acl=acl)
    r = c.get("/me/videos")
    assert r.status_code == 200
    body = r.text
    assert 'id="acl-form"' in body and 'name="email"' in body
    assert "m@example.com" in body and "member" in body
    assert f'data-acl-user="{member.id}"' in body  # remove button


def test_my_videos_xss_title_escaped() -> None:
    owner = _user()
    v = _my_video(owner.id, title="<script>alert(3)</script>")
    c, _, _, _ = _app4([v], user=owner)
    r = c.get("/me/videos")
    assert r.status_code == 200
    assert "<script>alert(3)</script>" not in r.text
    assert "&lt;script&gt;alert(3)&lt;/script&gt;" in r.text


def test_my_videos_empty_state() -> None:
    owner = _user()
    c, _, _, _ = _app4([], user=owner)
    r = c.get("/me/videos")
    assert r.status_code == 200
    assert 'data-testid="empty"' in r.text
