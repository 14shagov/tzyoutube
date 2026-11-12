"""CSRF guards: state-changing endpoints must reject cross-origin browsers.

Rule (docs/security.md): if a browser sent Origin, it must match the
request host; else if Referer is present, its origin must match;
headerless non-browser clients (curl) pass. SameSite=Lax cookies are the
second line of defense and are covered by auth cookie tests.
"""

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.main import create_app
from app.services.fakes import FakeUserRepo
from app.services.rate_limit import InMemoryRateLimiter


@pytest.fixture()
def client() -> TestClient:
    app = create_app()
    repo = FakeUserRepo()
    limiter = InMemoryRateLimiter()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: repo
    app.dependency_overrides[auth_api.get_rate_limiter] = lambda: limiter
    with TestClient(app) as c:
        yield c


def _register(client: TestClient) -> None:
    resp = client.post(
        "/auth/register",
        json={
            "email": "user1@example.com",
            "password": "Password123",
            "channel_name": "Channel One",
        },
    )
    assert resp.status_code == 201


class TestLoginCsrf:
    def test_foreign_origin_is_rejected(self, client: TestClient) -> None:
        _register(client)
        resp = client.post(
            "/auth/login",
            json={"email": "user1@example.com", "password": "Password123"},
            headers={"Origin": "http://evil.example"},
        )
        assert resp.status_code == 403

    def test_same_origin_passes_csrf(self, client: TestClient) -> None:
        _register(client)
        resp = client.post(
            "/auth/login",
            json={"email": "user1@example.com", "password": "Password123"},
            headers={"Origin": "http://testserver"},
        )
        assert resp.status_code == 200

    def test_foreign_referer_is_rejected(self, client: TestClient) -> None:
        _register(client)
        resp = client.post(
            "/auth/login",
            json={"email": "user1@example.com", "password": "Password123"},
            headers={"Referer": "http://evil.example/attack"},
        )
        assert resp.status_code == 403

    def test_same_host_referer_passes(self, client: TestClient) -> None:
        _register(client)
        resp = client.post(
            "/auth/login",
            json={"email": "user1@example.com", "password": "Password123"},
            headers={"Referer": "http://testserver/login"},
        )
        assert resp.status_code == 200

    def test_no_origin_headers_non_browser_client_passes(
        self, client: TestClient
    ) -> None:
        # curl and the owner's manual checks send neither header; CSRF is
        # a browser attack and browsers always set Origin on cross-site POST.
        _register(client)
        resp = client.post(
            "/auth/login",
            json={"email": "user1@example.com", "password": "Password123"},
        )
        assert resp.status_code == 200


class TestRegisterCsrf:
    def test_foreign_origin_is_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/auth/register",
            json={
                "email": "user1@example.com",
                "password": "Password123",
                "channel_name": "Channel One",
            },
            headers={"Origin": "http://evil.example"},
        )
        assert resp.status_code == 403


class TestLogoutCsrf:
    def test_foreign_origin_is_rejected(self, client: TestClient) -> None:
        _register(client)
        client.post(
            "/auth/login",
            json={"email": "user1@example.com", "password": "Password123"},
        )
        resp = client.post("/auth/logout", headers={"Origin": "http://evil.example"})
        assert resp.status_code == 403
