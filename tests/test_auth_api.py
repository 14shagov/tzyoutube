"""Unit tests for auth: registration, login, logout, roles, rate limit.

The HTTP layer is tested through the ASGI app with a fake database
(in-memory repository) injected via dependency overrides — no PostgreSQL
needed (ADR-005: services depend on interfaces, not HTTP or concrete DB).
"""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.main import create_app
from app.services import auth
from app.services.fakes import FakeUserRepo
from app.services.rate_limit import InMemoryRateLimiter, get_rate_limiter


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    repo = FakeUserRepo()
    limiter = InMemoryRateLimiter()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: repo
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    with TestClient(app) as c:
        yield c


def test_register_login_logout_flow(client: TestClient) -> None:
    r = client.post("/auth/register", json={
        "email": "user1@example.com",
        "password": "password123",
        "channel_name": "Channel One",
    })
    assert r.status_code == 201
    assert r.json()["channel_name"] == "Channel One"

    r = client.post("/auth/login", json={
        "email": "user1@example.com",
        "password": "password123",
    })
    assert r.status_code == 200
    assert "session_id" in r.cookies

    r = client.get("/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == "user1@example.com"

    r = client.post("/auth/logout")
    assert r.status_code == 204
    assert "session_id" not in r.cookies


def test_register_duplicate_email_conflict(client: TestClient) -> None:
    payload = {"email": "dup@example.com", "password": "password123", "channel_name": "Dup"}
    assert client.post("/auth/register", json=payload).status_code == 201
    payload2 = {"email": "dup@example.com", "password": "password123", "channel_name": "Dup 2"}
    assert client.post("/auth/register", json=payload2).status_code == 409


def test_register_duplicate_channel_name_conflict(client: TestClient) -> None:
    payload = {"email": "a@example.com", "password": "password123", "channel_name": "Same"}
    assert client.post("/auth/register", json=payload).status_code == 201
    payload2 = {"email": "b@example.com", "password": "password123", "channel_name": "Same"}
    assert client.post("/auth/register", json=payload2).status_code == 409


def test_register_rejects_short_password(client: TestClient) -> None:
    payload = {"email": "short@example.com", "password": "short", "channel_name": "Chan"}
    r = client.post("/auth/register", json=payload)
    assert r.status_code == 422


def test_login_wrong_password_generic_error(client: TestClient) -> None:
    client.post("/auth/register", json={
        "email": "g@example.com", "password": "password123", "channel_name": "G",
    })
    r = client.post("/auth/login", json={"email": "g@example.com", "password": "wrong-pass"})
    assert r.status_code == 401
    # Generic message: must not reveal whether the email exists.
    assert r.json()["detail"] == "invalid email or password"


def test_login_unknown_email_same_generic_error(client: TestClient) -> None:
    r = client.post("/auth/login", json={"email": "no@such.example", "password": "whatever"})
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid email or password"


def test_me_without_session_401(client: TestClient) -> None:
    r = client.get("/auth/me")
    assert r.status_code == 401


def test_blocked_user_session_rejected(client: TestClient) -> None:
    client.post("/auth/register", json={
        "email": "blocked@example.com", "password": "password123", "channel_name": "Blocked",
    })
    client.post("/auth/login", json={"email": "blocked@example.com", "password": "password123"})
    # Owner-side action: block the user directly in the fake repo.
    # (In production an admin endpoint does this; here we simulate the DB state.)
    app = client.app
    repo = app.dependency_overrides[auth_api.get_user_repo]()
    repo.users[repo.by_email["blocked@example.com"]].is_blocked = True

    r = client.get("/auth/me")
    assert r.status_code == 401


def test_role_guard_rejects_plain_user(client: TestClient) -> None:
    client.post("/auth/register", json={
        "email": "plain@example.com", "password": "password123", "channel_name": "Plain",
    })
    client.post("/auth/login", json={"email": "plain@example.com", "password": "password123"})
    # admin-only endpoint from group 3E (the old /admin/roles demo is gone)
    r = client.patch(f"/api/admin/users/{uuid.uuid4()}", json={"role": "admin"})
    assert r.status_code == 403


def test_login_rate_limited_after_5_failures(client: TestClient) -> None:
    for _ in range(5):
        client.post("/auth/login", json={"email": "rl@example.com", "password": "bad"})
    r = client.post("/auth/login", json={"email": "rl@example.com", "password": "bad"})
    assert r.status_code == 429


def test_session_expiry_is_enforced(client: TestClient) -> None:
    client.post("/auth/register", json={
        "email": "exp@example.com", "password": "password123", "channel_name": "Exp",
    })
    client.post("/auth/login", json={"email": "exp@example.com", "password": "password123"})
    app = client.app
    repo = app.dependency_overrides[auth_api.get_user_repo]()
    # Expire the session the client actually holds (the login cookie), not
    # whichever the repo stored last: with fast test hashing both sessions
    # can share one clock tick, so ordering by created_at is not reliable.
    held = repo.sessions[uuid.UUID(client.cookies["session_id"])]
    held.expires_at = auth.utc_now() - timedelta(seconds=1)
    assert client.get("/auth/me").status_code == 401


def test_logout_requires_session(client: TestClient) -> None:
    assert client.post("/auth/logout").status_code == 401
