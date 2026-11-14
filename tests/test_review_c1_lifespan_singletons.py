"""Review C1: Redis-backed clients must be created ONCE per process.

Before the fix, get_rate_limiter() built a NEW InMemoryRateLimiter (dev)
or a NEW Redis connection pool (prod) on every request — dev counters
reset per request (the limit never engaged) and prod leaked a pool per
call. get_queue() likewise built a new ArqTranscodeQueue (with its own
lazy arq pool) per request. Both singletons must now be created in the
FastAPI lifespan, stored on app.state, and closed on shutdown.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.api import auth as auth_api
from app.api import videos as videos_api
from app.core.config import Settings
from app.main import create_app
from app.services import rate_limit as rate_limit_module
from app.services.fakes import FakeUserRepo
from app.services.rate_limit import InMemoryRateLimiter, get_rate_limiter
from app.worker import queue as queue_module

IP = "203.0.113.10"  # documentation range (RFC 5737)


def _payload(n: int) -> dict:
    return {
        "email": f"c1user{n}@example.com",
        "password": "password123",
        "channel_name": f"C1 Channel {n}",
    }


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    repo = FakeUserRepo()
    # get_rate_limiter is deliberately NOT overridden: the real dependency
    # must serve the process-wide limiter from app.state.
    app.dependency_overrides[auth_api.get_user_repo] = lambda: repo
    with TestClient(app) as c:
        c.repo = repo  # type: ignore[attr-defined]
        yield c


class TestDevLimiterSharedAcrossRequests:
    def test_sixth_register_from_same_ip_is_429(self, client: TestClient) -> None:
        # RED before C1: a fresh limiter per request means the 6th is 201.
        for n in range(5):
            r = client.post("/auth/register", json=_payload(n), headers={"X-Real-IP": IP})
            assert r.status_code == 201, r.text
        r = client.post("/auth/register", json=_payload(99), headers={"X-Real-IP": IP})
        assert r.status_code == 429, r.text


class TestAppStateSingletons:
    def test_lifespan_sets_state_and_dependencies_read_it(self, client: TestClient) -> None:
        app = client.app
        assert isinstance(app.state.rate_limiter, InMemoryRateLimiter)
        assert app.state.queue is not None
        fake_request = SimpleNamespace(app=app)
        # The Depends providers must return the app.state singletons...
        assert get_rate_limiter(fake_request) is app.state.rate_limiter
        assert videos_api.get_queue(fake_request) is app.state.queue
        # ...and they must be stable across calls (no per-request instances).
        assert get_rate_limiter(fake_request) is get_rate_limiter(fake_request)
        assert videos_api.get_queue(fake_request) is videos_api.get_queue(fake_request)


class TestProdClientsCreatedOnce:
    def test_build_rate_limiter_prod_wraps_one_redis_client(self, monkeypatch) -> None:
        sentinel = object()
        monkeypatch.setattr(
            rate_limit_module.Redis, "from_url", lambda *a, **k: sentinel
        )
        prod = Settings(app_env="production", secret_key="a-real-secret-not-dev")
        limiter, client = rate_limit_module.build_rate_limiter(prod)
        assert client is sentinel
        assert isinstance(limiter, rate_limit_module.RedisRateLimiter)
        assert limiter._redis is sentinel

    def test_build_rate_limiter_dev_uses_memory_and_no_redis(self) -> None:
        limiter, client = rate_limit_module.build_rate_limiter(Settings())
        assert isinstance(limiter, InMemoryRateLimiter)
        assert client is None

    def test_build_queue_prod_creates_pool_once_and_aclose_closes_it(
        self, monkeypatch
    ) -> None:
        class FakePool:
            def __init__(self) -> None:
                self.closed = False

            async def aclose(self) -> None:
                self.closed = True

        pool = FakePool()

        async def fake_create_pool(_settings):
            return pool

        monkeypatch.setattr(queue_module, "create_pool", fake_create_pool)
        prod = Settings(app_env="production", secret_key="a-real-secret-not-dev")
        q = asyncio.run(queue_module.build_queue(prod))
        assert q._pool is pool
        asyncio.run(q.aclose())
        assert pool.closed

    def test_build_queue_dev_is_lazy_no_pool_at_startup(self) -> None:
        q = asyncio.run(queue_module.build_queue(Settings()))
        assert q._pool is None  # dev/tests: connects only on first enqueue


class TestLifespanShutdownClosesClients:
    def test_shutdown_closes_queue_and_redis(self, monkeypatch) -> None:
        closed = {"queue": False, "redis": False}

        class FakeQueue:
            async def aclose(self) -> None:
                closed["queue"] = True

        class FakeRedis:
            async def aclose(self) -> None:
                closed["redis"] = True

        monkeypatch.setattr(
            main_module,
            "build_rate_limiter",
            lambda settings: (InMemoryRateLimiter(), FakeRedis()),
        )

        async def fake_build_queue(_settings):
            return FakeQueue()

        monkeypatch.setattr(main_module, "build_queue", fake_build_queue)
        app = main_module.create_app()
        with TestClient(app):
            assert app.state.rate_limiter is not None
            assert app.state.queue is not None
        assert closed == {"queue": True, "redis": True}
