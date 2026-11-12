"""Role guards: dependencies that restrict endpoints by user role."""

from fastapi import Depends, HTTPException

from app.api.auth import get_current_user
from app.models import User


def require_role(*allowed: str):
    """Dependency factory: allow only users whose role is in `allowed`.

    Usage: `user: User = Depends(require_role("moderator", "admin"))`.
    401 for guests, 403 for authenticated users with the wrong role.
    """

    async def _guard(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(403, "insufficient permissions")
        return user

    return _guard
