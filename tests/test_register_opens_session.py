"""Register must open a session right away (m02086 task 5).

Choice: the register endpoint logs the user in (sets session cookie) —
the "register → upload" flow must work without a separate login step.
YouTube/GitHub behave the same way.
"""

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.main import create_app
from app.services.fakes import FakeUserRepo
from app.services.rate_limit import InMemoryRateLimiter

_PAYLOAD = {
    "email": "user1@example.com",
    "password": "Password123",
    "channel_name": "Channel One",
}


@pytest.fixture()
def client() -> TestClient:
    app = create_app()
    repo = FakeUserRepo()
    limiter = InMemoryRateLimiter()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: repo
    app.dependency_overrides[auth_api.get_rate_limiter] = lambda: limiter
    with TestClient(app) as c:
        yield c


class TestRegisterOpensSession:
    def test_register_sets_session_cookie(self, client: TestClient) -> None:
        resp = client.post("/auth/register", json=_PAYLOAD)
        assert resp.status_code == 201
        cookie = resp.headers.get("set-cookie", "")
        assert cookie.startswith("session_id="), cookie

    def test_register_then_me_without_login(self, client: TestClient) -> None:
        client.post("/auth/register", json=_PAYLOAD)
        me = client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["channel_name"] == "Channel One"

    def test_register_then_upload_without_login(self, client: TestClient) -> None:
        """The exact m02086 scenario: fresh user uploads right away."""
        from app.api import videos as videos_api

        client.post("/auth/register", json=_PAYLOAD)
        # Minimal mp4-ish payload: ftyp box header (magic bytes).
        head = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 16
        app = client.app
        app.dependency_overrides[videos_api.get_storage] = lambda: _FakeStorage()
        app.dependency_overrides[videos_api.get_queue] = lambda: _FakeQueue()
        app.dependency_overrides[videos_api.get_video_repo] = lambda: _FakeVideoRepo()
        resp = client.post(
            "/api/videos",
            files={"file": ("clip.mp4", head, "video/mp4")},
            data={"title": "My first upload"},
        )
        assert resp.status_code == 202, resp.text


class _FakeStorage:
    def put_stream(self, bucket, key, path):
        import os

        class _Obj:
            size = os.path.getsize(path)

        return _Obj()

    def ensure_buckets(self) -> None: ...


class _FakeQueue:
    async def enqueue_transcode(self, video_id) -> None: ...


class _FakeVideoRepo:
    def __init__(self) -> None:
        self.created = False

    async def create(self, video):
        self.created = True
        return video
