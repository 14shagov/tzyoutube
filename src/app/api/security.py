"""CSRF middleware: same-origin enforcement for state-changing methods.

Defense in depth alongside SameSite=Lax cookies (docs/security.md).

Source of truth for "is this Origin ours" (review m01882): the
ALLOWED_ORIGINS env list, with the request Host header as fallback.
The ASGI scope["server"] address is NEVER used — behind nginx it is the
container socket (e.g. 172.21.0.5:8000), which never matches what the
browser sent, so every browser POST would be rejected.

Rules:
- Origin present: must match an allowed origin (scheme+host+port,
  default ports 80/443 normalized away). "null" never matches.
- Else Referer present: its origin must match the same way.
- No Origin/Referer (curl, server-to-server): allowed — browsers always
  attach Origin to cross-site POSTs, so its absence means a non-browser
  client which cannot be CSRF'd.
- GET/HEAD/OPTIONS never blocked.
"""

import logging
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings, get_settings

logger = logging.getLogger("tzyoutube.csrf")

_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _normalize_origin(url: str) -> tuple[str, str] | None:
    """(scheme, host:port) or None if unparseable; default ports kept
    explicit so both sides of the comparison normalize identically."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    netloc = parts.netloc.lower()
    if not netloc:
        return None
    host = netloc
    port = _DEFAULT_PORTS.get(scheme)
    if ":" in netloc:
        host, _, port_str = netloc.rpartition(":")
        if not port_str.isdigit():
            return None
        port = int(port_str)
    return scheme, f"{host}:{port}"


def _allowed_origin_set(settings: Settings) -> set[tuple[str, str]]:
    origins: set[tuple[str, str]] = set()
    for raw in settings.allowed_origins.split(","):
        normalized = _normalize_origin(raw)
        if normalized is not None:
            origins.add(normalized)
    return origins


def get_csrf_settings() -> Settings:
    """Dependency seam: tests override this to inject Settings."""
    return get_settings()


class CsrfMiddleware:
    """Rejects cross-origin state-changing requests from browsers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in _STATE_CHANGING:
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        origin = headers.get("origin")
        referer = headers.get("referer")
        host_header = headers.get("host", "")

        settings = get_csrf_settings()
        allowed = _allowed_origin_set(settings)

        # Fallback: the Host header (nginx forwards it) is trusted as an
        # implicit allowed origin: http://<host>.
        if host_header:
            fallback = _normalize_origin(f"http://{host_header}")
            if fallback is not None:
                allowed.add(fallback)

        candidate = origin if origin is not None else referer
        if candidate is None:
            await self.app(scope, receive, send)
            return

        normalized = _normalize_origin(candidate)
        accepted = normalized is not None and normalized in allowed

        if not accepted:
            logger.warning(
                "csrf rejected: method=%s path=%s origin=%r referer=%r allowed=%s",
                scope.get("method"),
                scope.get("path"),
                origin,
                referer,
                sorted(settings.allowed_origins.split(",")),
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"detail":"cross-origin request rejected"}',
                }
            )
            return

        await self.app(scope, receive, send)
