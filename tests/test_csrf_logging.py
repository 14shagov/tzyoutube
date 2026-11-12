"""CSRF rejections must be visible in `docker compose logs web` (m01882 task 4)."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.api import security as security_module
from app.main import create_app
from app.services.fakes import FakeUserRepo
from app.services.rate_limit import InMemoryRateLimiter


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from app.core.config import Settings

    settings = Settings(_env_file=None)
    monkeypatch.setattr(security_module, "get_csrf_settings", lambda: settings)
    app = create_app()
    repo = FakeUserRepo()
    limiter = InMemoryRateLimiter()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: repo
    app.dependency_overrides[auth_api.get_rate_limiter] = lambda: limiter
    with TestClient(app) as c:
        yield c


def test_csrf_rejection_logs_warning_with_reason(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    payload = {
        "email": "user1@example.com",
        "password": "Password123",
        "channel_name": "Channel One",
    }
    with caplog.at_level(logging.WARNING, logger="tzyoutube.csrf"):
        resp = client.post(
            "/auth/register",
            json=payload,
            headers={"Origin": "http://evil.example"},
        )
    assert resp.status_code == 403
    csrf_records = [r for r in caplog.records if r.name == "tzyoutube.csrf"]
    assert csrf_records, "csrf rejection must be logged"
    message = csrf_records[0].getMessage()
    assert "csrf rejected" in message
    assert "http://evil.example" in message  # the offending origin
    assert "/auth/register" in message  # the path
    # No secrets or cookies in the log line.
    assert "session_id" not in message
    assert "password" not in message
