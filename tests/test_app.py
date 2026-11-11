"""Tests for the FastAPI app: /health and /docs."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_docs_available(client: AsyncClient) -> None:
    resp = await client.get("/docs")
    assert resp.status_code == 200


async def test_db_module_importable() -> None:
    from app.core import db  # noqa: F401
