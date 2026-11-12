"""Security settings of cookies and rate-limit client identification.

- session_id cookie: Secure flag must be set when APP_ENV=production
  (behind TLS) and absent in dev (plain http on localhost would drop it).
- rate limit client key: must come ONLY from the header the reverse
  proxy OVERWRITES (X-Real-IP set by nginx to $remote_addr), never from
  X-Forwarded-For, which the proxy APPENDS to — a client can spoof its
  left-most entry (review m01611, task 5).
"""

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.main import create_app
from app.services.fakes import FakeUserRepo
from app.services.rate_limit import InMemoryRateLimiter


def _make_client() -> TestClient:
    app = create_app()
    repo = FakeUserRepo()
    limiter = InMemoryRateLimiter()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: repo
    app.dependency_overrides[auth_api.get_rate_limiter] = lambda: limiter
    return TestClient(app)


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


def _login(client: TestClient, headers: dict | None = None, password: str = "Password123") -> object:
    return client.post(
        "/auth/login",
        json={"email": "user1@example.com", "password": password},
        headers=headers or {},
    )


class TestSecureCookie:
    def test_dev_cookie_has_no_secure_flag(self) -> None:
        with _make_client() as client:
            _register(client)
            resp = _login(client)
            assert resp.status_code == 200
            cookie_header = resp.headers.get("set-cookie", "")
            assert "session_id=" in cookie_header
            assert "secure" not in cookie_header.lower()

    def test_production_cookie_sets_secure_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import get_settings

        # Isolated Settings with production env and a real secret.
        settings = get_settings().model_copy(
            update={"app_env": "production", "secret_key": "x" * 32}
        )
        app = create_app()
        repo = FakeUserRepo()
        limiter = InMemoryRateLimiter()
        app.dependency_overrides[auth_api.get_user_repo] = lambda: repo
        app.dependency_overrides[auth_api.get_rate_limiter] = lambda: limiter
        app.dependency_overrides[get_settings] = lambda: settings
        with TestClient(app) as client:
            _register(client)
            resp = _login(client)
            assert resp.status_code == 200
            cookie_header = resp.headers.get("set-cookie", "")
            assert "secure" in cookie_header.lower(), cookie_header


class TestRateLimitClientKey:
    def test_spoofed_xff_left_entry_is_ignored(self) -> None:
        # A client cannot dodge or trigger someone else's rate limit by
        # prepending a fake IP to X-Forwarded-For: the app must key on the
        # LAST entry (what nginx added = real client) or on X-Real-IP only.
        with _make_client() as client:
            _register(client)
            resp = _login(
                client,
                headers={"X-Forwarded-For": "1.2.3.4, 10.0.0.1"},
            )
            assert resp.status_code == 200
            # The rate limiter key is internal; observable behavior: 5
            # failed logins through DIFFERENT spoofed XFF prefixes from the
            # same real client must still trip the limit.
            for _ in range(5):
                bad = _login(
                    client,
                    headers={
                        "X-Forwarded-For": f"9.9.9.{_}, 10.0.0.1",
                        "X-Real-IP": "10.0.0.1",
                    },
                    password="WrongPassword1",  # failed login on purpose
                )
                assert bad.status_code in (401, 429)
            sixth = _login(
                client,
                headers={"X-Real-IP": "10.0.0.1"},
            )
            assert sixth.status_code == 429, (
                "rate limit must key on the real client IP, not spoofable XFF"
            )

    def test_login_uses_real_ip_header_when_present(self) -> None:
        # X-Real-IP alone (set only by our nginx) is the trusted source.
        with _make_client() as client:
            _register(client)
            resp = _login(client, headers={"X-Real-IP": "203.0.113.10"})
            assert resp.status_code == 200
