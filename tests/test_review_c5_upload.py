"""C5 app-side: Content-Length pre-check on upload before reading the body.

nginx caps the body at 512m only on the upload location; the app rejects an
obviously-oversized Content-Length with 413 immediately (no temp file, no
body read), and a malformed Content-Length with 400.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import videos as videos_api

MAX = 512 * 1024 * 1024


class _ExplodingFile:
    """Fails the test if the body is read after the pre-check rejected it."""

    filename = "clip.mp4"

    async def read(self, _n: int) -> bytes:
        raise AssertionError("body must not be read once Content-Length is rejected")


def _settings() -> SimpleNamespace:
    return SimpleNamespace(max_upload_size=MAX)


async def _call(headers: dict[str, str], title: str = "x"):
    request = SimpleNamespace(headers=headers)
    return await videos_api.upload_video(
        file=_ExplodingFile(),
        title=title,
        description="",
        user=SimpleNamespace(id="00000000-0000-0000-0000-000000000001"),
        repo=None,
        storage=None,
        queue=None,
        settings=_settings(),
        request=request,
    )


async def test_oversized_content_length_413_before_reading_body() -> None:
    with pytest.raises(HTTPException) as exc:
        await _call({"content-length": str(MAX + 1)})
    assert exc.value.status_code == 413


async def test_malformed_content_length_400() -> None:
    with pytest.raises(HTTPException) as exc:
        await _call({"content-length": "not-a-number"})
    assert exc.value.status_code == 400


async def test_within_limit_passes_precheck() -> None:
    # A small Content-Length must not be rejected by the pre-check; the
    # next validation (empty title) proves we got past it.
    with pytest.raises(HTTPException) as exc:
        await _call({"content-length": "1024"}, title="")
    assert exc.value.status_code == 422
    assert "title" in exc.value.detail


async def test_missing_content_length_passes_precheck() -> None:
    with pytest.raises(HTTPException) as exc:
        await _call({}, title="")
    assert exc.value.status_code == 422
