"""HTTP layer for auth: register, login, logout, current user, role guards.

Maps domain errors from app.services.auth to HTTP responses; owns cookies.
"""

import uuid
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response
from fastapi import status as http_status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_db_session
from app.models import SessionToken, User
from app.schemas import UserLogin, UserPublic, UserRegister
from app.services import auth
from app.services.rate_limit import RateLimiterProtocol, get_rate_limiter

router = APIRouter(prefix="/auth", tags=["auth"])


class SqlUserRepo:
    """SQLAlchemy implementation of auth.UserRepo."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_channel_name(self, name: str) -> User | None:
        stmt = select(User).where(User.channel_name == name)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create_user(self, email: str, password_hash: str, channel_name: str) -> User:
        user = User(email=email, password_hash=password_hash, channel_name=channel_name)
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def create_session(
        self, user_id: uuid.UUID, expires_at: datetime
    ) -> SessionToken:
        token = SessionToken(user_id=user_id, expires_at=expires_at)
        self._session.add(token)
        await self._session.commit()
        await self._session.refresh(token)
        return token

    async def get_session_with_user(
        self, session_id: uuid.UUID
    ) -> tuple[SessionToken, User] | None:
        stmt = (
            select(SessionToken, User)
            .join(User, User.id == SessionToken.user_id)
            .where(SessionToken.id == session_id)
        )
        row = (await self._session.execute(stmt)).first()
        return (row[0], row[1]) if row else None

    async def delete_session(self, session_id: uuid.UUID) -> bool:
        stmt = delete(SessionToken).where(SessionToken.id == session_id)
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount > 0


async def get_user_repo(
    session: AsyncSession = Depends(get_db_session),
) -> SqlUserRepo:
    return SqlUserRepo(session)


def _set_session_cookie(
    response: Response,
    session_id: uuid.UUID,
    ttl_days: int,
    *,
    secure: bool = False,
) -> None:
    # Secure only in production: dev runs plain http on localhost, where
    # browsers drop Secure cookies (review m01611, task 5).
    response.set_cookie(
        "session_id",
        str(session_id),
        max_age=ttl_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


@router.post("/register", response_model=UserPublic, status_code=http_status.HTTP_201_CREATED)
async def register(
    response: Response,
    payload: UserRegister,
    repo: SqlUserRepo = Depends(get_user_repo),
    settings: Settings = Depends(get_settings),
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
    x_real_ip: Annotated[str, Header(alias="X-Real-IP")] = "",
) -> UserPublic:
    # Trust ONLY X-Real-IP (our nginx overwrites it with $remote_addr), same
    # rule as login. 5 registrations / 10 min per IP: the bcrypt cost-12
    # hash inside auth.register is a CPU-DoS target otherwise.
    client_key = x_real_ip.strip() or "unknown"
    try:
        await auth.throttle(
            limiter, "register", client_key, limit=5, window=timedelta(minutes=10)
        )
    except auth.RateLimited:
        raise HTTPException(429, "too many attempts, try later") from None
    try:
        user = await auth.register(repo, payload.email, payload.password, payload.channel_name)
    except auth.EmailTaken:
        raise HTTPException(409, "email already registered") from None
    except auth.ChannelNameTaken:
        raise HTTPException(409, "channel name already taken") from None
    # Registration opens the session right away (m02086 task 5): the
    # "register → upload" flow must not require a separate login step.
    token = await repo.create_session(
        user.id, auth.session_expiry(settings.session_ttl_days)
    )
    _set_session_cookie(
        response, token.id, settings.session_ttl_days, secure=settings.cookie_secure
    )
    return UserPublic.model_validate(user, from_attributes=True)


@router.post("/login", response_model=UserPublic)
async def login(
    response: Response,
    payload: UserLogin,
    repo: SqlUserRepo = Depends(get_user_repo),
    settings: Settings = Depends(get_settings),
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
    x_real_ip: Annotated[str, Header(alias="X-Real-IP")] = "",
) -> UserPublic:
    # Trust ONLY X-Real-IP: our nginx overwrites it with $remote_addr.
    # X-Forwarded-For is append-only upstream and its left entries are
    # client-spoofable (review m01611, task 5).
    client_key = x_real_ip.strip() or "unknown"
    try:
        token, user = await auth.login(
            repo,
            limiter,
            client_key,
            payload.email,
            payload.password,
            ttl_days=settings.session_ttl_days,
        )
    except auth.RateLimited:
        raise HTTPException(429, "too many attempts, try later") from None
    except (auth.InvalidCredentials, auth.AccountBlocked):
        # Same generic message: no user enumeration (docs/security.md).
        raise HTTPException(401, "invalid email or password") from None
    _set_session_cookie(
        response, token.id, settings.session_ttl_days, secure=settings.cookie_secure
    )
    return UserPublic.model_validate(user, from_attributes=True)


@router.post("/logout", status_code=http_status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    repo: SqlUserRepo = Depends(get_user_repo),
    session_id: str | None = Cookie(default=None),
) -> None:
    if session_id is None:
        raise HTTPException(401, "not authenticated")
    # Idempotent: a malformed cookie means "nothing to delete" — still 204
    # and the cookie is cleared, instead of a 500 from uuid.UUID(...).
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        sid = None
    if sid is not None:
        await repo.delete_session(sid)
    response.delete_cookie("session_id", path="/")


async def get_current_user(
    repo: SqlUserRepo = Depends(get_user_repo),
    session_id: str | None = Cookie(default=None),
) -> User:
    if session_id is None:
        raise HTTPException(401, "not authenticated")
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(401, "not authenticated") from None
    user = await auth.resolve_session(repo, sid)
    if user is None:
        raise HTTPException(401, "not authenticated")
    return user


@router.get("/me", response_model=UserPublic)
async def me(user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic.model_validate(user, from_attributes=True)
