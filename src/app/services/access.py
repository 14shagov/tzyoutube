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
    def presigned_get(
        self, bucket: str, key: str, ttl_seconds: int | None = None,
        response_headers: dict[str, str] | None = None, *, private: bool = False,
    ) -> str: ...


# Task 3: objects written before the content_type fix keep
# application/octet-stream in S3; the presigned URL overrides the response
# headers so the browser PLAYS the video (inline + video/mp4) instead of
# downloading it — this also covers already-uploaded objects.
PLAY_RESPONSE_HEADERS = {
    "response-content-type": "video/mp4",
    "response-content-disposition": "inline",
}


def can_view(video, user) -> bool:
    """Synchronous check: status, privacy, ownership, staff bypass.

    3E: a hidden (moderated) video stays visible to its owner and staff —
    they must see it to manage/unhide it. Everyone else gets False -> 404.
    ACL entries are checked separately in can_view_with_acl (they need I/O).
    """
    status = getattr(video, "status", None)
    if status == "deleted":
        return False
    if status == "hidden":
        if user is None:
            return False
        return getattr(user, "role", "user") in STAFF_ROLES or user.id == video.user_id
    if status != "ready":
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
    return storage.presigned_get(
        "videos", key, ttl_seconds=ttl_seconds, response_headers=PLAY_RESPONSE_HEADERS,
        private=False,
    )


def authorize_play_url(
    storage: DeliveryStorage,
    video,
    ttl_seconds: int | None = None,
    *,
    allow_hidden: bool = False,
) -> str | None:
    """Presigned URL after the caller verified can_view_with_acl.

    3E: hidden videos may still be played by owner/staff (allow_hidden=True,
    passed by view_or_none when the status is hidden); everyone else got
    False from the access check and never reaches presigning.
    """
    status = getattr(video, "status", None)
    if status == "ready" or status == "hidden" and allow_hidden:
        pass
    else:
        return None
    key = getattr(video, "video_key", None)
    if not key:
        return None
    # Task 7: private videos get the SHORT ttl (900s default), public 3600s.
    return storage.presigned_get(
        "videos", key, ttl_seconds=ttl_seconds, response_headers=PLAY_RESPONSE_HEADERS,
        private=bool(getattr(video, "is_private", False)),
    )


async def view_or_none(storage: DeliveryStorage, video, user, acl: AclRepo,
                       ttl_seconds: int | None = None) -> str | None:
    """Combined gate: full access check, then presign. None → caller sends 404."""
    if not await can_view_with_acl(video, user, acl):
        return None
    return authorize_play_url(
        storage, video, ttl_seconds=ttl_seconds,
        allow_hidden=getattr(video, "status", None) == "hidden",
    )
