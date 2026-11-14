"""Review B2: /auth/register must be rate-limited per client IP.

Registration hashes the password with bcrypt cost 12 (prod), so an
unthrottled endpoint is a CPU-DoS vector. The client key must come ONLY
from X-Real-IP (overwritten by nginx), exactly like /auth/login — never
from the spoofable X-Forwarded-For. Limit: 5 attempts per 10 minutes.
"""

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.main import create_app
from app.services.fakes import FakeUserRepo
from app.services.rate_limit import InMemoryRateLimiter, get_rate_limiter

IP_A = "203.0.113.10"  # documentation range (RFC 5737)
IP_B = "203.0.113.20"


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    repo = FakeUserRepo()
    limiter = InMemoryRateLimiter()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: repo
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    with TestClient(app) as c:
        c.repo = repo  # type: ignore[attr-defined]
        yield c


def _payload(n: int) -> dict:
    return {
        "email": f"user{n}@example.com",
        "password": "password123",
        "channel_name": f"Channel {n}",
    }


def _register(client: TestClient, n: int, ip: str | None = IP_A) -> object:
    headers = {"X-Real-IP": ip} if ip else {}
    return client.post("/auth/register", json=_payload(n), headers=headers)


class TestRegisterRateLimit:
    def test_sixth_register_from_same_ip_is_429(self, client: TestClient) -> None:
        for n in range(5):
            r = _register(client, n)
            assert r.status_code == 201, r.text
        r = _register(client, 99)
        assert r.status_code == 429

    def test_429_happens_before_any_user_is_created(self, client: TestClient) -> None:
        for n in range(5):
            _register(client, n)
        r = _register(client, 99)
        assert r.status_code == 429
        # The throttled attempt must not touch the repo (no bcrypt, no row).
        assert len(client.repo.users) == 5  # type: ignore[attr-defined]

    def test_other_ip_not_affected(self, client: TestClient) -> None:
        for n in range(5):
            _register(client, n, ip=IP_A)
        assert _register(client, 99, ip=IP_A).status_code == 429
        # A different real client IP has its own budget.
        assert _register(client, 100, ip=IP_B).status_code == 201

    def test_missing_header_shares_unknown_bucket(self, client: TestClient) -> None:
        for n in range(5):
            _register(client, n, ip=None)
        assert _register(client, 99, ip=None).status_code == 429

    def test_spoofed_xff_does_not_reset_the_limit(self, client: TestClient) -> None:
        for n in range(5):
            _register(client, n, ip=IP_A)
        r = client.post(
            "/auth/register",
            json=_payload(99),
            headers={"X-Real-IP": IP_A, "X-Forwarded-For": "1.2.3.4"},
        )
        assert r.status_code == 429
