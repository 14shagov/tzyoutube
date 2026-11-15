"""In-memory fakes for unit tests (ADR-005: interfaces over concretions)."""

import uuid
from datetime import UTC, datetime

from app.models import SessionToken, User


class FakeUserRepo:
    """Implements app.services.auth.UserRepo protocol in memory."""

    def __init__(self) -> None:
        self.users: dict[uuid.UUID, User] = {}
        self.sessions: dict[uuid.UUID, SessionToken] = {}
        self.by_email: dict[str, uuid.UUID] = {}
        self.by_channel: dict[str, uuid.UUID] = {}
        self.by_user_sessions: dict[uuid.UUID, list[uuid.UUID]] = {}

    def _add(self, user: User) -> None:
        self.users[user.id] = user
        self.by_email[user.email] = user.id
        self.by_channel[user.channel_name] = user.id

    async def get_by_email(self, email: str) -> User | None:
        uid = self.by_email.get(email)
        return self.users.get(uid) if uid else None

    async def get_by_channel_name(self, name: str) -> User | None:
        uid = self.by_channel.get(name)
        return self.users.get(uid) if uid else None

    async def create_user(self, email: str, password_hash: str, channel_name: str) -> User:
        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=password_hash,
            channel_name=channel_name,
            role="user",
            is_blocked=False,
            created_at=datetime.now(),
        )
        self._add(user)
        return user

    async def create_session(self, user_id: uuid.UUID, expires_at: datetime) -> SessionToken:
        token = SessionToken(
            id=uuid.uuid4(),
            user_id=user_id,
            expires_at=expires_at,
            created_at=datetime.now(UTC),
        )
        self.sessions[token.id] = token
        self.by_user_sessions.setdefault(user_id, []).append(token.id)
        return token

    async def get_session_with_user(
        self, session_id: uuid.UUID
    ) -> tuple[SessionToken, User] | None:
        token = self.sessions.get(session_id)
        if token is None:
            return None
        user = self.users.get(token.user_id)
        if user is None:
            return None
        return token, user

    async def delete_session(self, session_id: uuid.UUID) -> bool:
        token = self.sessions.pop(session_id, None)
        if token is None:
            return False
        lst = self.by_user_sessions.get(token.user_id, [])
        if session_id in lst:
            lst.remove(session_id)
        return True

    async def purge_expired_sessions(self) -> int:
        now = datetime.now(UTC)
        expired = [sid for sid, t in self.sessions.items() if t.expires_at <= now]
        for sid in expired:
            token = self.sessions.pop(sid)
            lst = self.by_user_sessions.get(token.user_id, [])
            if sid in lst:
                lst.remove(sid)
        return len(expired)

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return self.users.get(user_id)

    async def get_by_ids(self, ids) -> list:
        return [self.users[i] for i in ids if i in self.users]

    async def update_user(self, user: User) -> User:
        self.users[user.id] = user
        return user
