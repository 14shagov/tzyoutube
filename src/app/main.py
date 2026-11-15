"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import pages as pages_api
from app.api import auth as auth_api
from app.api import channels as channels_api
from app.api import moderation as moderation_api
from app.api import playlists as playlists_api
from app.api import videos as videos_api
from app.api.security import CsrfMiddleware
from app.core.config import get_settings
from app.core.storage import BUCKETS, get_storage
from app.services.rate_limit import build_rate_limiter
from app.worker.queue import build_queue

logger = logging.getLogger("tzyoutube.startup")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Bootstrap storage buckets and the process-wide Redis clients.

    Buckets (review m02188): idempotent, run off the event loop (blocking
    minio-py client); a storage outage must not kill the app — log and
    continue, uploads will surface the error loudly if it persists.

    Redis clients (review C1): the rate limiter and the transcode queue
    are created ONCE here and stored on app.state; the get_rate_limiter /
    get_queue dependencies just read app.state. Created per request they
    leaked a pool each call (prod) or reset counters every request (dev).
    Both are closed on shutdown.
    """
    import asyncio

    settings = get_settings()
    limiter, redis_client = build_rate_limiter(settings)
    app.state.rate_limiter = limiter
    app.state.redis_client = redis_client
    app.state.queue = await build_queue(settings)
    logger.info("process-wide rate limiter and transcode queue created")

    try:
        storage = get_storage()
        await asyncio.to_thread(storage.ensure_buckets)
        logger.info("storage buckets ensured: %s", sorted(BUCKETS))
    except Exception:
        logger.exception("ensure_buckets failed at startup; uploads may 500 until storage is reachable")
    try:
        yield
    finally:
        queue = getattr(app.state, "queue", None)
        if queue is not None:
            await queue.aclose()
        if redis_client is not None:
            await redis_client.aclose()


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

    class HealthOut(BaseModel):
        status: str

    @app.get("/health", response_model=HealthOut)
    async def health() -> HealthOut:
        return HealthOut(status="ok")

    app.include_router(auth_api.router)
    app.include_router(videos_api.router)
    app.include_router(videos_api.feed_router)
    app.include_router(channels_api.router)
    app.include_router(channels_api.api_router)
    app.include_router(playlists_api.router)
    app.include_router(moderation_api.router)
    app.include_router(pages_api.router)

    # F1 (ADR-007): static assets (CSS/JS) are served same-origin from the
    # repo — no CDN, so CSP script-src 'self' holds (nginx adds the header).
    from fastapi.staticfiles import StaticFiles

    app.mount(
        "/static",
        StaticFiles(directory=str(pages_api.STATIC_DIR)),
        name="static",
    )

    # F7: HTML error pages for browser routes; API paths keep JSON details.
    # A JSON request (Accept: application/json, X-Requested-With, or an
    # /api, /auth, /docs path) gets the plain FastAPI JSON error instead.
    @app.exception_handler(StarletteHTTPException)
    async def _http_error_page(request: Request, exc: StarletteHTTPException):
        is_api = (
            request.url.path.startswith(("/api", "/auth", "/docs", "/openapi.json"))
            or "application/json" in request.headers.get("accept", "")
            or request.headers.get("x-requested-with") == "XMLHttpRequest"
        )
        if is_api:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return pages_api.templates.TemplateResponse(
            request,
            "error.html",
            {
                "request": request,
                "user": None,
                "brand": pages_api.BRAND,
                "code": exc.status_code,
                "message": str(exc.detail),
            },
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception):
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        is_api = (
            request.url.path.startswith(("/api", "/auth", "/docs", "/openapi.json"))
            or "application/json" in request.headers.get("accept", "")
            or request.headers.get("x-requested-with") == "XMLHttpRequest"
        )
        if is_api:
            return JSONResponse({"detail": "internal server error"}, status_code=500)
        return pages_api.templates.TemplateResponse(
            request,
            "error.html",
            {
                "request": request,
                "user": None,
                "brand": pages_api.BRAND,
                "code": 500,
                "message": "Внутренняя ошибка сервера",
            },
            status_code=500,
        )

    return app


app = create_app()
