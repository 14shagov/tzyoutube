"""Presigning must be a purely LOCAL HMAC computation — no network (real-stack bug).

minio-py without an explicit ``region`` calls GetBucketLocation over the
network on every presigned_* call (``Minio._get_region`` -> ``_url_open``
with ``?location=``). Inside the web container the public endpoint host is
not reachable, so GET /api/videos/{id} died with MaxRetryError
(host='localhost', port=80, url: /videos?location=).

These tests use a REAL Minio client pair built exactly like
``app.core.storage.get_storage`` does (public endpoint + keys from
settings) — NO autospec stub on the client — and forbid ANY network at
the urllib3 layer. Signing must still produce a valid URL whose host is
the PUBLIC endpoint.
"""

import uuid
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from minio import Minio
from urllib3 import PoolManager

from app.api import videos as videos_api
from app.core import storage as storage_module
from app.core.config import get_settings
from app.main import create_app
from app.services import access


def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any urllib3 request during the test is a bug (presign must be offline)."""

    def _boom(self, *args, **kwargs):
        raise AssertionError(
            f"network call during presign: urllib3 PoolManager.urlopen{args[1] if len(args) > 1 else ''}"
        )

    monkeypatch.setattr(PoolManager, "urlopen", _boom)


def real_storage() -> storage_module.Storage:
    """Build the Storage exactly like get_storage(): two real Minio clients."""
    settings = get_settings()
    client = Minio(
        settings.s3_endpoint,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        secure=settings.s3_secure,
        region=settings.s3_region,
    )
    presign_client = Minio(
        settings.s3_public_endpoint,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        secure=False,  # public entry is plain HTTP on port 80
        region=settings.s3_region,
    )
    return storage_module.Storage(
        client,
        presign_client=presign_client,
        presigned_ttl=settings.presigned_ttl_seconds,
    )


def _ready_video(**overrides) -> SimpleNamespace:
    defaults: dict = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "title": "Clip",
        "description": "d",
        "status": "ready",
        "is_private": False,
        "video_key": "videos/x.mp4",
        "thumbnail_key": "thumbnails/x.jpg",
        "duration_seconds": 10,
        "size_bytes": 100,
        "view_count": 3,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestPresignIsOffline:
    def test_presigned_get_does_no_network_and_uses_public_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        forbid_network(monkeypatch)
        storage = real_storage()
        url = storage.presigned_get("videos", "videos/x.mp4")
        parts = urlsplit(url)
        assert parts.scheme == "http"
        # Signed URL host must be the PUBLIC endpoint (what the browser uses),
        # not the internal silo:9000.
        assert parts.netloc == get_settings().s3_public_endpoint
        assert parts.path == "/videos/videos/x.mp4"
        query = parts.query
        assert "X-Amz-Signature=" in query and "X-Amz-Expires=" in query

    def test_get_storage_clients_carry_explicit_region(self) -> None:
        # Without an explicit region minio-py asks the server ?location= by
        # network on every presign. Pin the constructor argument.
        import inspect

        src = inspect.getsource(storage_module.get_storage)
        assert "region=settings.s3_region" in src
        settings = get_settings()
        assert settings.s3_region == "us-east-1"  # explicit default, no lookup


class _NoNetworkAcl:
    async def is_allowed(self, video_id, user_id) -> bool:
        return False


class TestEveryPresignPathIsOffline:
    """Audit (task 3): every code path that produces a presigned URL."""

    def test_video_play_url_public(self, monkeypatch: pytest.MonkeyPatch) -> None:
        forbid_network(monkeypatch)
        storage = real_storage()
        url = access.video_play_url(storage, _ready_video())
        assert url is not None and urlsplit(url).netloc == get_settings().s3_public_endpoint

    def test_authorize_play_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        forbid_network(monkeypatch)
        storage = real_storage()
        url = access.authorize_play_url(storage, _ready_video())
        assert url is not None and "X-Amz-Signature=" in url

    @pytest.mark.asyncio
    async def test_view_or_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        forbid_network(monkeypatch)
        storage = real_storage()
        url = await access.view_or_none(storage, _ready_video(), None, _NoNetworkAcl())
        assert url is not None and "X-Amz-Signature=" in url

    def test_private_play_url_is_not_presigned_without_access_check(self) -> None:
        # video_play_url must refuse private videos; the authorized path is
        # the only presigner for them.
        storage = real_storage()
        assert access.video_play_url(storage, _ready_video(is_private=True)) is None

    def test_thumbnails_are_presigned_never_public_media(self) -> None:
        # ADR-009: the thumbnails bucket is PRIVATE; every thumb (public or
        # private) is a presigned URL, never a bare /media/{key} path.
        public = videos_api._thumb_url(_ready_video(), real_storage())
        assert public is not None and not public.startswith("/media/")
        assert "X-Amz-Signature=" in urlsplit(public).query
        private = videos_api._thumb_url(_ready_video(is_private=True), real_storage())
        assert private is not None and not private.startswith("/media/")
        assert "X-Amz-Signature=" in urlsplit(private).query
        assert videos_api._thumb_url(_ready_video(thumbnail_key=None), real_storage()) is None


class _FakeVideoRepo:
    def __init__(self, videos: list) -> None:
        self.by_id = {v.id: v for v in videos}

    async def get(self, video_id):
        return self.by_id.get(video_id)


class _FakeFeedRepo:
    def __init__(self, videos: list) -> None:
        self.videos = videos

    async def public_feed(self, limit: int = 20, offset: int = 0):
        return self.videos[offset : offset + limit]

    async def search(self, query: str, limit: int = 20, offset: int = 0):
        q = query.lower()
        return [v for v in self.videos if q in v.title.lower()][offset : offset + limit]


class TestFeedSearchCardWithRealPresignClient:
    """Task 4: feed/search/card work when videos are ready, with a REAL
    presign client and ONLY the network layer forbidden."""

    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        forbid_network(monkeypatch)  # whole app run must not touch the network
        video = _ready_video()
        app = create_app()
        app.dependency_overrides[videos_api.get_video_repo] = lambda: _FakeVideoRepo([video])
        app.dependency_overrides[videos_api.get_feed_repo] = lambda: _FakeFeedRepo([video])
        # Real storage (real Minio clients, explicit region) — no autospec:
        app.dependency_overrides[videos_api.get_storage] = lambda: real_storage()
        with TestClient(app) as c:
            c._test_video = video  # type: ignore[attr-defined]
            yield c

    def test_feed_200_with_presigned_thumbnail(self, client: TestClient) -> None:
        r = client.get("/api/feed")
        assert r.status_code == 200
        item = r.json()["items"][0]
        assert item["thumbnail_url"] and not item["thumbnail_url"].startswith("/media/")
        assert "X-Amz-Signature=" in urlsplit(item["thumbnail_url"]).query
        assert item["view_count"] == 3

    def test_search_200(self, client: TestClient) -> None:
        r = client.get("/api/search", params={"q": "clip"})
        assert r.status_code == 200
        assert len(r.json()["items"]) == 1

    def test_video_card_200_with_signed_play_url_on_public_host(
        self, client: TestClient
    ) -> None:
        video = client._test_video  # type: ignore[attr-defined]
        r = client.get(f"/api/videos/{video.id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ready"
        parts = urlsplit(body["play_url"])
        assert parts.netloc == get_settings().s3_public_endpoint
        assert "X-Amz-Signature=" in parts.query
        thumb = body["thumbnail_url"]
        assert thumb and not thumb.startswith("/media/")
        assert "X-Amz-Signature=" in urlsplit(thumb).query
