"""CSRF middleware: same-origin enforcement for state-changing methods.

Defense in depth alongside SameSite=Lax cookies (docs/security.md):
- If Origin is present, its host:port must equal the request host.
- Else if Referer is present, its origin must equal the request host.
- No Origin/Referer (curl, mobile app, server-to-server): allowed —
  browsers always attach Origin to cross-site POSTs, so its absence
  means a non-browser client which cannot be CSRF'd.

CSRF tokens are unnecessary here: the app has no cross-site POST
use-cases, and Origin check + SameSite=Lax covers the browser surface.
"""

from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _host_of(url: str) -> str:
    """Normalize host[:port], dropping default ports, for comparison."""
    parts = urlsplit(url)
    netloc = (parts.netloc or parts.path).lower()
    scheme = parts.scheme.lower() or "http"
    if ":" in netloc:
        host, _, port = netloc.rpartition(":")
        if port.isdigit() and int(port) == _DEFAULT_PORTS.get(scheme):
            return host
    return netloc


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
        request_host = scope.get("server")
        # Drop the port when it is the scheme default (80 here): browsers
        # omit it in Origin/Referer, scope always carries it.
        port = request_host[1] if request_host else 0
        request_host_str = (
            request_host[0] if port in (80, 443, 0) else f"{request_host[0]}:{port}"
        ).lower() if request_host else ""

        bad_origin = origin is not None and _host_of(origin) != request_host_str
        bad_referer = (
            origin is None
            and referer is not None
            and _host_of(referer) != request_host_str
        )

        if bad_origin or bad_referer:
            response_start = {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"application/json")],
            }
            await send(response_start)
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"detail":"cross-origin request rejected"}',
                }
            )
            return

        await self.app(scope, receive, send)
