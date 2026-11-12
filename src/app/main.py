"""FastAPI application factory."""

from fastapi import Depends, FastAPI

from app.api import auth as auth_api
from app.api import videos as videos_api
from app.api.deps import require_role
from app.core.config import get_settings
from app.models import User


def create_app() -> FastAPI:
    # Load settings eagerly: the Settings validator fails fast in production
    # if SECRET_KEY is left at the insecure dev default.
    get_settings()

    app = FastAPI(
        title="tzyoutube",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )

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
