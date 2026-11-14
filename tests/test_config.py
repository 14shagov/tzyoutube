"""Tests for Settings (env-driven config)."""

import pytest

from app.core.config import Settings


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.app_env == "dev"
    assert s.max_upload_size == 512 * 1024 * 1024
    assert s.presigned_ttl_seconds == 3600  # public videos
    assert s.presigned_ttl_private_seconds == 900  # private videos: short-lived links
    assert s.session_ttl_days == 14


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "real-secret")
    monkeypatch.setenv("MAX_UPLOAD_SIZE", "1048576")
    s = Settings(_env_file=None)
    assert s.is_production
    assert s.secret_key == "real-secret"
    assert s.max_upload_size == 1048576


def test_production_requires_real_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError):
        Settings(_env_file=None)
