"""Task 3 (red): presigned PLAY URLs must pin the response headers.

Objects uploaded before the content_type fix keep
application/octet-stream in S3, so the browser downloads them. The presigned
URL itself can override the response headers:
  response-content-type=video/mp4
  response-content-disposition=inline
Signing is offline (region pinned), so these tests forbid any network at the
urllib3 layer and use the real Minio presign client.
"""

import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from test_presign_offline import forbid_network, real_storage

from app.core.config import get_settings
from app.services import access


def _ready_video(**overrides) -> SimpleNamespace:
    defaults: dict = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "title": "Clip",
        "status": "ready",
        "is_private": False,
        "video_key": "videos/x.mp4",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


class _Acl:
    async def is_allowed(self, video_id, user_id) -> bool:
        return False


class TestPlayUrlResponseHeaders:
    def test_video_play_url_pins_content_type_and_inline(self, monkeypatch) -> None:
        forbid_network(monkeypatch)
        url = access.video_play_url(real_storage(), _ready_video())
        assert url is not None
        q = _query(url)
        assert q.get("response-content-type") == ["video/mp4"], url
        assert q.get("response-content-disposition") == ["inline"], url
        assert "X-Amz-Signature=" in urlsplit(url).query

    def test_authorize_play_url_pins_content_type_and_inline(self, monkeypatch) -> None:
        forbid_network(monkeypatch)
        url = access.authorize_play_url(real_storage(), _ready_video(is_private=True))
        assert url is not None
        q = _query(url)
        assert q.get("response-content-type") == ["video/mp4"], url
        assert q.get("response-content-disposition") == ["inline"], url

    @pytest.mark.asyncio
    async def test_view_or_none_pins_content_type_and_inline(self, monkeypatch) -> None:
        forbid_network(monkeypatch)
        url = await access.view_or_none(real_storage(), _ready_video(), None, _Acl())
        assert url is not None
        q = _query(url)
        assert q.get("response-content-type") == ["video/mp4"], url
        assert q.get("response-content-disposition") == ["inline"], url

    def test_storage_presigned_get_accepts_response_headers_offline(
        self, monkeypatch
    ) -> None:
        forbid_network(monkeypatch)
        storage = real_storage()
        url = storage.presigned_get(
            "videos", "videos/x.mp4",
            response_headers={
                "response-content-type": "video/mp4",
                "response-content-disposition": "inline",
            },
        )
        assert urlsplit(url).netloc == get_settings().s3_public_endpoint
        q = _query(url)
        assert q.get("response-content-type") == ["video/mp4"], url
        assert q.get("response-content-disposition") == ["inline"], url
