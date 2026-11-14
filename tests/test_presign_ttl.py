"""Task 7 (red): presigned URL TTL must be configurable and short.

Private videos must not get 4-hour links: defaults are
PRESIGNED_TTL_SECONDS=3600 (public) and PRESIGNED_TTL_PRIVATE_SECONDS=900
(private). Signing is offline, so network is forbidden at the urllib3 layer
and the real Minio presign client is used.
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


def _expires(url: str) -> int:
    return int(parse_qs(urlsplit(url).query)["X-Amz-Expires"][0])


class TestTtlDefaults:
    def test_public_ttl_default_3600(self) -> None:
        assert get_settings().presigned_ttl_seconds == 3600

    def test_private_ttl_default_900(self) -> None:
        assert get_settings().presigned_ttl_private_seconds == 900


class _Acl:
    async def is_allowed(self, video_id, user_id) -> bool:
        return False


class TestTtlInSignedUrl:
    def test_public_play_url_expires_3600(self, monkeypatch) -> None:
        forbid_network(monkeypatch)
        url = access.video_play_url(real_storage(), _ready_video(is_private=False))
        assert _expires(url) == 3600, url

    def test_private_authorized_url_expires_900(self, monkeypatch) -> None:
        forbid_network(monkeypatch)
        url = access.authorize_play_url(real_storage(), _ready_video(is_private=True))
        assert _expires(url) == 900, url

    def test_public_authorized_url_expires_3600(self, monkeypatch) -> None:
        forbid_network(monkeypatch)
        url = access.authorize_play_url(real_storage(), _ready_video(is_private=False))
        assert _expires(url) == 3600, url

    @pytest.mark.asyncio
    async def test_view_or_none_private_uses_900(self, monkeypatch) -> None:
        forbid_network(monkeypatch)
        video = _ready_video(is_private=True)

        class _Owner:
            id = video.user_id
            role = "user"

        url = await access.view_or_none(real_storage(), video, _Owner(), _Acl())
        assert url is not None
        assert _expires(url) == 900, url

    def test_explicit_ttl_overrides_default(self, monkeypatch) -> None:
        forbid_network(monkeypatch)
        url = access.video_play_url(real_storage(), _ready_video(), ttl_seconds=60)
        assert _expires(url) == 60, url
