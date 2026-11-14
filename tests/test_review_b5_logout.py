"""Review B5: /auth/logout with a malformed session cookie must be idempotent.

A garbage cookie value (not a UUID) previously crashed uuid.UUID(...) with an
unhandled ValueError → 500. Logout is a state-clearing operation: an invalid
cookie means "nothing to delete" → still 204, cookie still cleared. Only a
MISSING cookie is 401.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.main import create_app
from app.services.fakes import FakeUserRepo
from app.services.rate_limit import InMemoryRateLimiter, get_rate_limiter


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    repo = FakeUserRepo()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: repo
    app.dependency_overrides[get_rate_limiter] = lambda: InMemoryRateLimiter()
    with TestClient(app) as c:
        c.repo = repo  # type: ignore[attr-defined]
        yield c


class TestLogoutIdempotent:
    def test_missing_cookie_is_401(self, client: TestClient) -> None:
        assert client.post("/auth/logout").status_code == 401

    def test_garbage_cookie_is_204_not_500(self, client: TestClient) -> None:
        client.cookies.set("session_id", "not-a-uuid")
        r = client.post("/auth/logout")
        assert r.status_code == 204, r.text

    def test_garbage_cookie_still_clears_the_cookie(self, client: TestClient) -> None:
        client.cookies.set("session_id", "not-a-uuid!!")
        r = client.post("/auth/logout")
        assert r.status_code == 204
        set_cookie = r.headers.get("set-cookie", "")
        assert "session_id=" in set_cookie, set_cookie  # deletion header present

    def test_unknown_but_wellformed_uuid_is_204(self, client: TestClient) -> None:
        client.cookies.set("session_id", str(uuid.uuid4()))
        r = client.post("/auth/logout")
        assert r.status_code == 204, r.text

    def test_real_session_is_deleted(self, client: TestClient) -> None:
        client.post(
            "/auth/register",
            json={"email": "u@example.com", "password": "password123", "channel_name": "U"},
        )
        assert len(client.repo.sessions) == 1  # type: ignore[attr-defined]
        r = client.post("/auth/logout")
        assert r.status_code == 204
        assert len(client.repo.sessions) == 0  # type: ignore[attr-defined]
