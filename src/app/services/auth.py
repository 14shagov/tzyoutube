"""Auth domain: password hashing, sessions, registration and login logic.

Services must not know about HTTP (ADR-005): they take a repository
protocol and raise domain errors; the API layer maps them to responses.
"""

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

import bcrypt

from app.models import SessionToken, User

# bcrypt with cost 12 (docs/security.md). We use the bcrypt package
# directly: passlib 1.7.4 is unmaintained and incompatible with bcrypt 5.x.
_BCRYPT_ROUNDS = 12


class AuthError(Exception):
    """Base class for auth domain errors."""


class EmailTaken(AuthError):
    pass


class ChannelNameTaken(AuthError):
    pass


class InvalidCredentials(AuthError):
    pass


class AccountBlocked(AuthError):
    pass


class RateLimited(AuthError):
    pass


class Role(StrEnum):
    USER = "user"
    MODERATOR = "moderator"
    ADMIN = "admin"


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        # Malformed stored hash must never crash login; fail closed.
        return False


def utc_now() -> datetime:
    return datetime.now(UTC)


def session_expiry(ttl_days: int) -> datetime:
    return utc_now() + timedelta(days=ttl_days)


class UserRepo(Protocol):
    """Storage interface for auth (implemented by SQLAlchemy, faked in tests)."""

    async def get_by_email(self, email: str) -> User | None: ...

    async def get_by_channel_name(self, name: str) -> User | None: ...

    async def create_user(self, email: str, password_hash: str, channel_name: str) -> User: ...

    async def create_session(self, user_id: uuid.UUID, expires_at: datetime) -> SessionToken: ...

    async def get_session_with_user(self, session_id: uuid.UUID) -> tuple[SessionToken, User] | None: ...

    async def delete_session(self, session_id: uuid.UUID) -> bool: ...


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: int, window: timedelta) -> bool:
        """Record an event for `key`; return True if still under `limit`."""


async def register(
    repo: UserRepo, email: str, password: str, channel_name: str
) -> User:
    if await repo.get_by_email(email) is not None:
        raise EmailTaken()
    if await repo.get_by_channel_name(channel_name) is not None:
        raise ChannelNameTaken()
    user = await repo.create_user(email, hash_password(password), channel_name)
    return user


async def login(
    repo: UserRepo,
    limiter: RateLimiter,
    client_key: str,
    email: str,
    password: str,
    ttl_days: int,
) -> tuple[SessionToken, User]:
    if not await limiter.hit(f"login:{client_key}", limit=5, window=timedelta(minutes=10)):
        raise RateLimited()
    user = await repo.get_by_email(email)
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentials()
    if user.is_blocked:
        raise AccountBlocked()
    token = await repo.create_session(user.id, session_expiry(ttl_days))
    return token, user


async def resolve_session(
    repo: UserRepo, session_id: uuid.UUID
) -> User | None:
    pair = await repo.get_session_with_user(session_id)
    if pair is None:
        return None
    token, user = pair
    if token.expires_at <= utc_now():
        return None
    if user.is_blocked:
        return None
    return user
