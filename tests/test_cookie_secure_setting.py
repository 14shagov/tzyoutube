"""COOKIE_SECURE: independent setting, default off (plain-HTTP deploys work).

Review m01882 task 5: tying Secure to APP_ENV=production breaks login on
a clean VM without TLS — the browser silently refuses to store the
cookie. Secure becomes an explicit deployment decision (HTTPS only).
"""

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.core.config import get_settings
from app.main import create_app
from app.services.fakes import FakeUserRepo
from app.services.rate_limit import InMemoryRateLimiter


def _make_app(monkeypatch: pytest.MonkeyPatch, **settings_kwargs) -> TestClient:
    from app.core.config import Settings

    defaults = {"_env_file": None, "secret_key": "x" * 32}
    defaults.update(settings_kwargs)
    settings = Settings(**defaults)
    # Depends() binds get_settings at import time; the override table is
    # the only reliable injection point.
    monkeypatch.setattr("app.api.auth.get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    repo = FakeUserRepo()
    limiter = InMemoryRateLimiter()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: repo
    app.dependency_overrides[auth_api.get_rate_limiter] = lambda: limiter
    return TestClient(app)


_PAYLOAD = {
    "email": "user1@example.com",
    "password": "Password123",
    "channel_name": "Channel One",
}


class TestCookieSecureSetting:
    def test_default_is_off_even_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _make_app(monkeypatch, app_env="production") as client:
            client.post("/auth/register", json=_PAYLOAD)
            resp = client.post(
                "/auth/login",
                json={"email": _PAYLOAD["email"], "password": _PAYLOAD["password"]},
            )
            assert resp.status_code == 200
            cookie = resp.headers.get("set-cookie", "")
            assert "secure" not in cookie.lower(), (
                "plain-HTTP VM deploy must keep working: Secure must default off"
            )

    def test_enabled_sets_secure_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _make_app(monkeypatch, cookie_secure=True) as client:
            client.post("/auth/register", json=_PAYLOAD)
            resp = client.post(
                "/auth/login",
                json={"email": _PAYLOAD["email"], "password": _PAYLOAD["password"]},
            )
            assert resp.status_code == 200
            cookie = resp.headers.get("set-cookie", "")
            assert "secure" in cookie.lower()

    def test_setting_exists_with_default_false(self) -> None:
        from app.core.config import Settings

        settings = Settings(_env_file=None)
        assert settings.cookie_secure is False
