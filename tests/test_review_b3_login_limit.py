"""Review B3: login rate limit must count ONLY failed attempts, and unknown
emails must still run a bcrypt verify (timing equalization — no user
enumeration through response latency).

Limit stays 5 attempts / 10 min per X-Real-IP, but a SUCCESSFUL login
consumes no budget, and once the budget is spent the 429 is raised BEFORE
any hashing (a valid password must not bypass the limit).
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.main import create_app
from app.services import auth
from app.services.fakes import FakeUserRepo
from app.services.rate_limit import (
    InMemoryRateLimiter,
    RedisRateLimiter,
    get_rate_limiter,
)

IP = "203.0.113.10"  # RFC 5737 documentation range


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    repo = FakeUserRepo()
    limiter = InMemoryRateLimiter()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: repo
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    with TestClient(app) as c:
        c.repo = repo  # type: ignore[attr-defined]
        c.limiter = limiter  # type: ignore[attr-defined]
        yield c


def _register(client: TestClient, email: str = "u@example.com", password: str = "password123") -> None:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": password, "channel_name": "Chan " + email},
        headers={"X-Real-IP": IP},
    )
    assert r.status_code == 201, r.text


def _login(client: TestClient, email: str, password: str, ip: str = IP):
    return client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers={"X-Real-IP": ip},
    )


class TestOnlyFailuresCounted:
    def test_many_successful_logins_never_trip_limit(self, client: TestClient) -> None:
        _register(client)
        for _ in range(8):
            r = _login(client, "u@example.com", "password123")
            assert r.status_code == 200, r.text

    def test_failures_then_successes_stay_under_limit(self, client: TestClient) -> None:
        _register(client)
        for _ in range(4):
            assert _login(client, "u@example.com", "wrong-pass").status_code == 401
        # Successful logins must not consume the failure budget.
        for _ in range(3):
            assert _login(client, "u@example.com", "password123").status_code == 200

    def test_unknown_email_attempts_count_as_failures(self, client: TestClient) -> None:
        _register(client)
        for _ in range(5):
            assert _login(client, "ghost@example.com", "whatever").status_code == 401
        # 6th attempt from the same IP — even with VALID credentials — is 429.
        assert _login(client, "u@example.com", "password123").status_code == 429

    def test_success_does_not_record_limiter_event(self, client: TestClient) -> None:
        _register(client)
        _login(client, "u@example.com", "password123")
        key = f"login:{IP}"
        assert key not in client.limiter._events or not client.limiter._events[key]  # type: ignore[attr-defined]


class TestTimingEqualization:
    def test_unknown_email_runs_dummy_bcrypt_verify(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        real = auth.verify_password

        def spy(raw: str, hashed: str) -> bool:
            calls.append(hashed)
            return real(raw, hashed)

        monkeypatch.setattr(auth, "verify_password", spy)
        r = _login(client, "ghost@example.com", "whatever")
        assert r.status_code == 401
        assert calls, "unknown email must still run one bcrypt verify (timing equalization)"

    def test_dummy_hash_follows_current_bcrypt_rounds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(auth, "_DUMMY_HASH", None, raising=False)
        monkeypatch.setattr(auth, "_BCRYPT_ROUNDS", 4)
        try:
            h = auth._dummy_password_hash()
            assert h.startswith("$2b$04$"), h  # cost is encoded in the hash prefix
        finally:
            monkeypatch.setattr(auth, "_DUMMY_HASH", None, raising=False)


class TestCheckPeekDoesNotRecord:
    async def test_inmemory_check_is_a_peek(self) -> None:
        lim = InMemoryRateLimiter()
        window = timedelta(minutes=1)
        for _ in range(10):
            assert await lim.check("k", limit=5, window=window) is True
        # Peeking did not consume anything: still 5 hits allowed.
        for _ in range(5):
            assert await lim.hit("k", limit=5, window=window) is True
        assert await lim.hit("k", limit=5, window=window) is False
        assert await lim.check("k", limit=5, window=window) is False

    async def test_redis_check_is_a_peek(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        redis = MagicMock()
        pipe = MagicMock()
        redis.pipeline.return_value = pipe
        pipe.execute = AsyncMock(return_value=[None, 0])
        lim = RedisRateLimiter(redis)
        assert await lim.check("k", limit=5, window=timedelta(minutes=1)) is True
        redis.zadd.assert_not_called()  # check must NOT record
        redis.expire.assert_not_called()
