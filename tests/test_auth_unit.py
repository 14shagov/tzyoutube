"""Unit tests for auth models, hashing, and session token helpers."""

from datetime import timedelta

from app.models import Base, SessionToken, User
from app.services import auth


def test_models_module_exposes_auth_tables() -> None:
    # Table names match db-schema.md (snake_case plurals).
    assert User.__tablename__ == "users"
    assert SessionToken.__tablename__ == "sessions"
    assert "email" in User.__table__.columns
    assert "channel_name" in User.__table__.columns
    assert "role" in User.__table__.columns
    assert "is_blocked" in User.__table__.columns
    assert Base is not None


def test_password_hash_is_bcrypt_and_verifies() -> None:
    raw = "correct horse battery staple"
    hashed = auth.hash_password(raw)
    assert hashed != raw
    assert hashed.startswith("$2")  # bcrypt prefix ($2a$/$2b$/$2y$)
    assert auth.verify_password(raw, hashed) is True
    assert auth.verify_password("wrong", hashed) is False


def test_verify_password_malformed_hash_fails_closed() -> None:
    assert auth.verify_password("x", "not-a-bcrypt-hash") is False


def test_new_session_token_expiry_uses_settings_ttl() -> None:
    ttl_days = 14
    expires = auth.session_expiry(ttl_days)
    delta = expires - auth.utc_now()
    assert timedelta(days=13, hours=23) < delta <= timedelta(days=14)


def test_utc_now_returns_timezone_aware() -> None:
    now = auth.utc_now()
    assert now.tzinfo is not None
