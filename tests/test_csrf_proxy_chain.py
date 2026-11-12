"""Reproduction of the real nginx -> container request chain (review m01882).

TestClient fakes a coherent scope (server == Host), which is why unit
tests never saw the production failure: behind nginx the socket server
is the container's internal address (e.g. 172.21.0.5:8000), while the
browser sends Host: localhost and Origin: http://localhost.

These tests build the ASGI scope by hand the way uvicorn behind nginx
does: server = container socket, headers carry the public Host.
"""


from app.api.security import CsrfMiddleware


class _Recorder:
    """Captures what the wrapped app received (or not)."""

    def __init__(self) -> None:
        self.called = False
        self.status = 200
        self.body = b""

    async def __call__(self, scope, receive, send):
        self.called = True

        async def _send(message):
            if message["type"] == "http.response.start":
                self.status = message["status"]
            elif message["type"] == "http.response.body":
                self.body += message.get("body", b"")

        await send(
            {"type": "http.response.start", "status": 200, "headers": []}
        )
        await send({"type": "http.response.body", "body": b"ok"})


def _scope(
    method: str = "POST",
    server: tuple[str, int] = ("172.21.0.5", 8000),
    headers: dict[str, str] | None = None,
) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": "/auth/register",
        "raw_path": b"/auth/register",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": server,
        "client": ("203.0.113.10", 12345),
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
    }


async def _run(scope: dict) -> tuple[_Recorder, bool]:
    """Run CsrfMiddleware over the scope; return (inner app, was 403 sent)."""
    inner = _Recorder()
    middleware = CsrfMiddleware(inner)
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await middleware(scope, receive, send)
    rejected = any(
        m["type"] == "http.response.start" and m["status"] == 403 for m in sent
    )
    return inner, rejected


class TestRealProxyChain:
    async def test_swagger_post_behind_nginx_is_not_rejected(self) -> None:
        # Exact real chain (m01882): socket server is the container,
        # Host/Origin say localhost. Must PASS the middleware.
        inner, rejected = await _run(
            _scope(
                headers={"host": "localhost", "origin": "http://localhost"}
            )
        )
        assert inner.called, "request must reach the app behind nginx"
        assert not rejected

    async def test_foreign_origin_behind_nginx_still_rejected(self) -> None:
        inner, rejected = await _run(
            _scope(headers={"host": "localhost", "origin": "http://evil.example"})
        )
        assert rejected, "foreign Origin must still be rejected"
        assert not inner.called
