"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.api import auth as auth_api
from app.api import videos as videos_api
from app.api.deps import require_role
from app.api.security import CsrfMiddleware
from app.core.config import get_settings
from app.core.storage import BUCKETS, get_storage
from app.models import User

logger = logging.getLogger("tzyoutube.startup")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Bootstrap storage buckets (review m02188: NoSuchBucket on first upload).

    Idempotent: safe on every restart. Runs off the event loop (blocking
    minio-py client); a storage outage must not kill the app — log and
    continue, uploads will surface the error loudly if it persists.
    """
    import asyncio

    try:
        storage = get_storage()
        await asyncio.to_thread(storage.ensure_buckets)
        logger.info("storage buckets ensured: %s", sorted(BUCKETS))
    except Exception:
        logger.exception("ensure_buckets failed at startup; uploads may 500 until storage is reachable")
    yield


def create_app() -> FastAPI:
    # Load settings eagerly: the Settings validator fails fast in production
    # if SECRET_KEY is left at the insecure dev default.
    get_settings()

    app = FastAPI(
        title="tzyoutube",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    # CSRF: outermost middleware, rejects cross-origin state-changing
    # browser requests (docs/security.md).
    app.add_middleware(CsrfMiddleware)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_api.router)
    app.include_router(videos_api.router)
    app.include_router(videos_api.feed_router)

    # Temporary demo endpoint to prove the role guard wiring works.
    # Real admin endpoints arrive in stage 4 (plan 4.4).
    @app.get("/admin/roles")
    async def admin_roles(user: User = Depends(require_role("admin"))) -> dict[str, str]:
        return {"role": user.role}

    return app


app = create_app()
