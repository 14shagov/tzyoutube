"""Video access control and presigned delivery rules.

Privacy (FR-9, docs/security.md): a private video is visible only to its
owner, ACL members (stage 3 video_acl), and staff; everyone else must
receive 404 — the caller maps `False` to 404, never 403.
"""

import uuid
from typing import Protocol

STAFF_ROLES = {"moderator", "admin"}


class AclRepo(Protocol):
    async def is_allowed(self, video_id: uuid.UUID, user_id: uuid.UUID) -> bool: ...


class DeliveryStorage(Protocol):
    def presigned_get(self, bucket: str, key: str, ttl_seconds: int | None = None) -> str: ...


def can_view(video, user) -> bool:
    """Synchronous check: status, privacy, ownership, staff bypass.

    ACL entries are checked separately in can_view_with_acl (they need I/O).
    """
    if getattr(video, "status", None) != "ready":
        return False
    if not getattr(video, "is_private", False):
        return True
    if user is None:
        return False
    if getattr(user, "role", "user") in STAFF_ROLES:
        return True
    return user.id == video.user_id


async def can_view_with_acl(video, user, acl: AclRepo) -> bool:
    """Full check including the private-video whitelist."""
    if can_view(video, user):
        return True
    if user is None or not getattr(video, "is_private", False):
        return False
    if getattr(video, "status", None) != "ready":
        return False
    return await acl.is_allowed(video.id, user.id)


def video_play_url(storage: DeliveryStorage, video, ttl_seconds: int | None = None) -> str | None:
    """Presigned playback URL for PUBLIC videos only; None otherwise.

    Private videos must go through the authorized path
    (can_view_with_acl → authorize_play_url) so the URL is never
    generated without an explicit access decision.
    """
    if getattr(video, "status", None) != "ready":
        return None
    key = getattr(video, "video_key", None)
    if not key:
        return None
    if getattr(video, "is_private", False):
        return None
    return storage.presigned_get("videos", key, ttl_seconds=ttl_seconds)


def authorize_play_url(storage: DeliveryStorage, video, ttl_seconds: int | None = None) -> str | None:
    """Presigned URL after the caller verified can_view_with_acl."""
    if getattr(video, "status", None) != "ready":
        return None
    key = getattr(video, "video_key", None)
    if not key:
        return None
    return storage.presigned_get("videos", key, ttl_seconds=ttl_seconds)


async def view_or_none(storage: DeliveryStorage, video, user, acl: AclRepo,
                       ttl_seconds: int | None = None) -> str | None:
    """Combined gate: full access check, then presign. None → caller sends 404."""
    if not await can_view_with_acl(video, user, acl):
        return None
    return authorize_play_url(storage, video, ttl_seconds=ttl_seconds)
