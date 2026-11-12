"""CSRF origin checks against ALLOWED_ORIGINS + Host fallback (m01882 fix).

Source of truth for "is this Origin ours": the ALLOWED_ORIGINS env list;
fallback — the request Host header (nginx forwards it). The socket
server address (container IP:8000) must NEVER be used: it is not what
the browser sees.
"""

import contextlib

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.api import security as security_module
from app.main import create_app
from app.services.fakes import FakeUserRepo
from app.services.rate_limit import InMemoryRateLimiter


@contextlib.contextmanager
def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    allowed_origins: str = "http://localhost,http://127.0.0.1",
):
    from app.core.config import Settings

    settings = Settings(_env_file=None, allowed_origins=allowed_origins)
    monkeypatch.setattr(security_module, "get_csrf_settings", lambda: settings)
    app = create_app()
    repo = FakeUserRepo()
    limiter = InMemoryRateLimiter()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: repo
    app.dependency_overrides[auth_api.get_rate_limiter] = lambda: limiter
    with TestClient(app) as client:
        yield client


def _register_payload(n: int) -> dict:
    return {
        "email": f"user{n}@example.com",
        "password": "Password123",
        "channel_name": f"Channel {n}",
    }


class TestAllowedOriginList:
    def test_own_origin_from_list_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _make_client(monkeypatch) as client:
            resp = client.post(
                "/auth/register",
                json=_register_payload(1),
                headers={"Origin": "http://localhost"},
            )
            assert resp.status_code == 201

    def test_foreign_origin_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _make_client(monkeypatch) as client:
            resp = client.post(
                "/auth/register",
                json=_register_payload(2),
                headers={"Origin": "http://evil.example"},
            )
            assert resp.status_code == 403

    def test_custom_allowed_origin_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Deploy on a VM by IP: owner sets ALLOWED_ORIGINS=http://203.0.113.10
        with _make_client(monkeypatch, "http://203.0.113.10") as client:
            resp = client.post(
                "/auth/register",
                json=_register_payload(3),
                headers={"Origin": "http://203.0.113.10"},
            )
            assert resp.status_code == 201

    def test_no_origin_non_browser_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # curl and the owner's manual checks send neither header.
        with _make_client(monkeypatch) as client:
            resp = client.post("/auth/register", json=_register_payload(4))
            assert resp.status_code == 201

    def test_origin_null_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with _make_client(monkeypatch) as client:
            resp = client.post(
                "/auth/register",
                json=_register_payload(5),
                headers={"Origin": "null"},
            )
            assert resp.status_code == 403

    def test_referer_without_origin_checked_the_same(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _make_client(monkeypatch) as client:
            resp = client.post(
                "/auth/register",
                json=_register_payload(6),
                headers={"Referer": "http://evil.example/attack"},
            )
            assert resp.status_code == 403

    def test_own_referer_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with _make_client(monkeypatch) as client:
            resp = client.post(
                "/auth/register",
                json=_register_payload(7),
                headers={"Referer": "http://localhost/register"},
            )
            assert resp.status_code == 201

    def test_get_and_head_not_touched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _make_client(monkeypatch) as client:
            resp = client.get("/health")
            assert resp.status_code == 200

    def test_default_port_normalization(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # http://localhost:80 == http://localhost after normalization.
        with _make_client(monkeypatch, "http://localhost:80") as client:
            resp = client.post(
                "/auth/register",
                json=_register_payload(8),
                headers={"Origin": "http://localhost"},
            )
            assert resp.status_code == 201
