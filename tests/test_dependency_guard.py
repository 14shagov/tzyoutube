"""Guard test (review item A1): every route's dependency providers must construct.

Walks all APIRoutes of the app (including routers added via include_router,
which FastAPI >= 0.162 wraps in _IncludedRouter) and resolves their
dependency trees with FastAPI's own solver, overriding ONLY the DB session
and the external clients (object storage, arq queue / Redis, rate limiter).
Any other exception during provider construction — e.g. a repository built
without its session, which surfaces as a 500 on every viewing endpoint —
fails the test.
"""

import uuid
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from unittest.mock import create_autospec

import pytest
from fastapi import HTTPException
from fastapi.dependencies.utils import solve_dependencies
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute, _IncludedRouter
from minio import Minio
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.api import videos as videos_api
from app.core import db as db_module
from app.core import storage as storage_module
from app.main import create_app
from app.models import SessionToken, User
from app.services.rate_limit import InMemoryRateLimiter, get_rate_limiter
from app.worker.queue import ArqTranscodeQueue


def _walk_api_routes(routes) -> list[APIRoute]:
    """Flatten app.routes: _IncludedRouter wrappers hide their APIRoutes."""
    found: list[APIRoute] = []
    for r in routes:
        if isinstance(r, APIRoute):
            found.append(r)
        elif isinstance(r, _IncludedRouter):
            found.extend(_walk_api_routes(r.original_router.routes))
        elif hasattr(r, "routes"):
            found.extend(_walk_api_routes(r.routes))
    return found


def _api_routes(app) -> list[APIRoute]:
    return _walk_api_routes(app.routes)


def _route_ids() -> list[str]:
    return [f"{min(r.methods)} {r.path}" for r in _api_routes(create_app())]


def _guard_app():
    """App with ONLY get_db_session and external clients overridden.

    The autospec'd session is primed so auth resolution succeeds with a
    real User/SessionToken pair: get_current_user / _optional_user must not
    mask the repo providers sitting behind them.
    """
    app = create_app()
    session = create_autospec(AsyncSession, instance=True)
    user = User(
        id=uuid.uuid4(),
        email="guard@example.com",
        password_hash="x",
        channel_name="guard",
        role="admin",
    )
    token = SessionToken(
        id=uuid.uuid4(),
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    # autospec makes AsyncSession.execute an AsyncMock, but its result's
    # .first() comes out async too — replace with a plain callable so
    # SqlUserRepo.get_session_with_user gets a real (token, user) row.
    session.execute.return_value.first = lambda: (token, user)
    # Entity lookups (session.get(Model, id)) must 404 cleanly instead of
    # returning a truthy Mock, so endpoints that fetch by id are testable.
    session.get.return_value = None

    async def _session_override():
        yield session

    storage = storage_module.Storage(create_autospec(Minio, instance=True))
    app.dependency_overrides[db_module.get_db_session] = _session_override
    app.dependency_overrides[videos_api.get_storage] = lambda: storage
    app.dependency_overrides[videos_api.get_queue] = (
        lambda: create_autospec(ArqTranscodeQueue, instance=True)
    )
    app.dependency_overrides[get_rate_limiter] = (
        lambda: create_autospec(InMemoryRateLimiter, instance=True)
    )
    return app


def _fill_path_params(route: APIRoute) -> str:
    """Concrete path: {video_id} -> a real UUID, anything else -> 'x'."""
    path = route.path
    for param in route.dependant.path_params:
        value = str(uuid.uuid4()) if "id" in param.name else "x"
        path = path.replace("{" + param.name + "}", value)
    return path


def _make_request(app, route: APIRoute, inner_stack: AsyncExitStack,
                  function_stack: AsyncExitStack) -> Request:
    path = _fill_path_params(route)
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": min(route.methods),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"cookie", f"session_id={uuid.uuid4()}".encode()),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "app": app,
        # FastAPI >= 0.162 solve_dependencies requires both stacks in scope
        # (routing.py sets them per request).
        "fastapi_inner_astack": inner_stack,
        "fastapi_function_astack": function_stack,
    }

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive=_receive)


@pytest.mark.asyncio
@pytest.mark.parametrize("idx", range(len(_api_routes(create_app()))), ids=_route_ids())
async def test_route_providers_construct(idx: int) -> None:
    app = _guard_app()
    route = _api_routes(app)[idx]
    async with (
        AsyncExitStack() as inner_stack,
        AsyncExitStack() as function_stack,
    ):
        request = _make_request(app, route, inner_stack, function_stack)
        try:
            await solve_dependencies(
                request=request,
                dependant=route.dependant,
                dependency_overrides_provider=app,
                async_exit_stack=inner_stack,
                embed_body_fields=False,
            )
        except (HTTPException, RequestValidationError):
            # Auth guards firing or a missing request body are input-level
            # outcomes, not provider-construction failures.
            return
