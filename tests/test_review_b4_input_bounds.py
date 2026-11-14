"""Review B4: over-length / malformed inputs must be rejected with 422 by
request validation, never reach the DB (Video.title is String(200),
VideoView.viewer_fingerprint is String(64)) — a 500 there is a crash, not an
input error. A guest_id cookie with characters outside [A-Za-z0-9_-] must be
replaced by a fresh sanitized token, not stored as-is.
"""

import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.api import videos as videos_api
from app.main import create_app

GUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _video() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        title="Clip",
        description="d",
        status="ready",
        is_private=False,
        video_key="videos/x.mp4",
        thumbnail_key=None,
        duration_seconds=10,
        size_bytes=100,
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


class FakeViewRepo:
    def __init__(self) -> None:
        self.views: list[tuple] = []

    async def recent_view(self, video_id, fingerprint, since):
        return False

    async def add_view(self, video_id, user_id, fingerprint):
        self.views.append((video_id, user_id, fingerprint))

    async def increment_view_count(self, video_id):
        pass


class FakeStorage:
    def presigned_get(self, bucket, key, ttl_seconds=None, response_headers=None):
        return f"http://example.com/{bucket}/{key}"


class FakeAclRepo:
    async def is_allowed(self, video_id, user_id):
        return False


class FakeQueue:
    async def enqueue_transcode(self, video_id):
        pass


class FakeUser:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.email = "u@example.com"
        self.channel_name = "U"
        self.role = "user"
        self.is_blocked = False


def _make_app(video):
    app = create_app()
    view_repo = FakeViewRepo()
    app.dependency_overrides[videos_api.get_video_repo] = lambda: FakeVideoRepo([video])
    app.dependency_overrides[videos_api.get_view_repo] = lambda: view_repo
    app.dependency_overrides[videos_api.get_storage] = lambda: FakeStorage()
    app.dependency_overrides[videos_api.get_acl] = lambda: FakeAclRepo()
    app.dependency_overrides[videos_api.get_queue] = lambda: FakeQueue()
    app.dependency_overrides[auth_api.get_current_user] = lambda: FakeUser()
    return app, view_repo


@pytest.fixture
def video() -> SimpleNamespace:
    return _video()


@pytest.fixture
def client(video):
    app, view_repo = _make_app(video)
    with TestClient(app) as c:
        c.view_repo = view_repo  # type: ignore[attr-defined]
        c.video = video  # type: ignore[attr-defined]
        yield c


class TestUploadTitleBound:
    def _upload(self, client: TestClient, title: str):
        return client.post(
            "/api/videos",
            files={"file": ("x.mp4", b"\x00" * 64, "video/mp4")},
            data={"title": title, "description": "d"},
        )

    def test_title_over_200_chars_rejected_422_not_500(self, client: TestClient) -> None:
        r = self._upload(client, "x" * 201)
        assert r.status_code == 422, r.text

    def test_title_exactly_200_chars_passes_validation(self, client: TestClient) -> None:
        # 200 is allowed by validation; the fake-file content is then rejected
        # by magic-byte validation with 415 — but never 422 for length.
        r = self._upload(client, "x" * 200)
        assert r.status_code != 422, r.text


class TestGuestIdSanitized:
    def _view(self, client: TestClient, cookie: str | None):
        if cookie is not None:
            client.cookies.set("guest_id", cookie)
        else:
            client.cookies.delete("guest_id")
        return client.post(f"/api/videos/{client.video.id}/view")  # type: ignore[attr-defined]

    def test_guest_id_over_64_chars_rejected_422(self, client: TestClient) -> None:
        r = self._view(client, "a" * 65)
        assert r.status_code == 422, r.text

    def test_guest_id_with_invalid_chars_replaced_by_fresh_token(self, client: TestClient) -> None:
        bad = "bad id<script>%_"  # 18 chars, passes length, fails charset
        r = self._view(client, bad)
        assert r.status_code == 204, r.text
        fingerprint = client.view_repo.views[-1][2]  # type: ignore[attr-defined]
        assert fingerprint != bad
        assert GUEST_ID_RE.match(fingerprint), fingerprint

    def test_valid_guest_id_kept_as_is(self, client: TestClient) -> None:
        good = "abc-DEF_123"
        r = self._view(client, good)
        assert r.status_code == 204, r.text
        assert client.view_repo.views[-1][2] == good  # type: ignore[attr-defined]

    def test_missing_guest_id_issues_fresh_token(self, client: TestClient) -> None:
        r = self._view(client, None)
        assert r.status_code == 204, r.text
        fingerprint = client.view_repo.views[-1][2]  # type: ignore[attr-defined]
        assert GUEST_ID_RE.match(fingerprint), fingerprint
        assert "guest_id=" in r.headers.get("set-cookie", "")
