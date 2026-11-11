"""FastAPI application factory."""

from fastapi import FastAPI

from app.core.config import get_settings


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

    return app


app = create_app()
