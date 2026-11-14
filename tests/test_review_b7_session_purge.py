"""Review B7: expired sessions must be purged (on successful login).

Session rows accumulate forever otherwise (DB bloat + dead tokens lying
around). Strategy per review: opportunistic purge inside login success —
no cron needed. Failed logins must NOT purge (no extra writes on the
attacker-controlled path).
"""

import uuid
from datetime import timedelta
from unittest.mock import create_autospec

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import auth as auth_api
from app.api.auth import SqlUserRepo
from app.main import create_app
from app.services import auth
from app.services.fakes import FakeUserRepo
from app.services.rate_limit import InMemoryRateLimiter, get_rate_limiter

IP = "203.0.113.10"


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    repo = FakeUserRepo()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: repo
    app.dependency_overrides[get_rate_limiter] = lambda: InMemoryRateLimiter()
    with TestClient(app) as c:
        c.repo = repo  # type: ignore[attr-defined]
        yield c


def _register(client: TestClient) -> None:
    r = client.post(
        "/auth/register",
        json={"email": "u@example.com", "password": "password123", "channel_name": "U"},
        headers={"X-Real-IP": IP},
    )
    assert r.status_code == 201, r.text


def _login(client: TestClient, password: str = "password123"):
    return client.post(
        "/auth/login",
        json={"email": "u@example.com", "password": password},
        headers={"X-Real-IP": IP},
    )


def _expire_all(client: TestClient) -> None:
    repo = client.repo  # type: ignore[attr-defined]
    for token in repo.sessions.values():
        token.expires_at = auth.utc_now() - timedelta(seconds=1)


class TestFakeRepoPurge:
    async def test_purge_removes_only_expired(self) -> None:
        repo = FakeUserRepo()
        user = await repo.create_user("u@example.com", "h", "U")
        valid = await repo.create_session(user.id, auth.utc_now() + timedelta(days=1))
        expired = await repo.create_session(user.id, auth.utc_now() - timedelta(seconds=1))
        removed = await repo.purge_expired_sessions()
        assert removed == 1
        assert expired.id not in repo.sessions
        assert valid.id in repo.sessions
        assert expired.id not in repo.by_user_sessions[user.id]

    async def test_purge_with_nothing_expired(self) -> None:
        repo = FakeUserRepo()
        assert await repo.purge_expired_sessions() == 0


class TestSqlRepoPurge:
    async def test_delete_expired_and_commit(self) -> None:
        session = create_autospec(AsyncSession, instance=True)
        session.execute.return_value.rowcount = 3
        repo = SqlUserRepo(session)
        removed = await repo.purge_expired_sessions()
        assert removed == 3
        session.execute.assert_awaited_once()
        stmt = session.execute.call_args[0][0]
        sql = str(stmt.compile()).upper()
        assert "DELETE" in sql and "EXPIRES_AT" in sql
        session.commit.assert_awaited_once()


class TestLoginTriggersPurge:
    def test_successful_login_purges_expired_sessions(self, client: TestClient) -> None:
        _register(client)  # creates session #1 (register opens a session)
        _expire_all(client)
        assert len(client.repo.sessions) == 1  # type: ignore[attr-defined]
        r = _login(client)
        assert r.status_code == 200, r.text
        repo = client.repo  # type: ignore[attr-defined]
        # Only the fresh login session remains; the expired one was purged.
        assert len(repo.sessions) == 1
        held = uuid.UUID(r.cookies["session_id"])
        assert held in repo.sessions

    def test_failed_login_does_not_purge(self, client: TestClient) -> None:
        _register(client)
        _expire_all(client)
        r = _login(client, password="wrong-pass")
        assert r.status_code == 401
        assert len(client.repo.sessions) == 1  # type: ignore[attr-defined] — untouched
