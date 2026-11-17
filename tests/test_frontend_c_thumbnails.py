"""C: thumbnail access + worker frame offset + UI placeholder.

Contract (owner m00002 task C):
- private / hidden / not-ready / deleted videos NEVER get an ANONYMOUS
  thumb URL. Decision (ADR-009): presigned URLs for ALL thumbnails and the
  `thumbnails` bucket stays PRIVATE (no public-read policy in
  ensure_buckets) — so nginx /media/thumbnails/<key> 403s and a thumb is
  only obtainable via a presigned URL issued after an access check.
  Public ready videos therefore get a working (signed) thumb without error.
- worker extracts the frame NOT at t=0: offset = min(1s, 10% of duration),
  never past the end.
- UI: if a thumb fails to load, a neutral placeholder shows WITHOUT inline
  JS (CSP script-src 'self'): external app.js swaps in /static/thumb-ph.svg.
"""

import uuid
from pathlib import Path
from unittest.mock import create_autospec
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from minio import Minio

from app.api import auth as auth_api
from app.api import videos as videos_api
from app.core import storage as storage_module
from app.core.config import get_settings
from app.main import create_app
from app.services import transcode as tc
from app.services.fakes import FakeUserRepo
from test_presign_offline import _ready_video, forbid_network, real_storage
from test_viewing_api import FakeFeedRepo, FakeVideoRepo, _video

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "src" / "app" / "templates"
STATIC = ROOT / "src" / "app" / "static"


# --- worker frame offset -----------------------------------------------------


class TestThumbnailOffset:
    def test_offset_is_10_percent_capped_at_1s(self) -> None:
        assert tc.thumbnail_offset_seconds(20.0) == 1.0   # 10% = 2s -> capped 1s
        assert tc.thumbnail_offset_seconds(5.0) == 0.5    # 10% = 0.5s
        assert tc.thumbnail_offset_seconds(0.5) == pytest.approx(0.05)

    def test_offset_never_past_end_and_never_zero(self) -> None:
        for d in (0.05, 0.5, 1.0, 3.7, 30.0, 3600.0):
            off = tc.thumbnail_offset_seconds(d)
            assert 0 < off <= d, f"offset {off} not within (0, {d}]"
            assert off <= 1.0

    def test_offset_unknown_duration_falls_back_to_1s(self) -> None:
        assert tc.thumbnail_offset_seconds(None) == 1.0
        assert tc.thumbnail_offset_seconds(0) == 1.0

    def test_build_ffmpeg_args_uses_dynamic_offset_not_hardcoded_1(self) -> None:
        args = tc.build_ffmpeg_args("in.raw", "out.mp4", "thumb.jpg", thumb_offset=0.5)
        joined = args
        i = joined.index("-ss")
        assert joined[i + 1] == "0.5"
        # default keeps the legacy 1s behaviour for callers that don't pass it
        assert tc.build_ffmpeg_args("i", "o", "t")[tc.build_ffmpeg_args("i", "o", "t").index("-ss") + 1] == "1"


# --- bucket privacy (autospec Minio, offline) --------------------------------


class TestBucketPrivacy:
    def test_ensure_buckets_never_grants_public_read(self) -> None:
        client = create_autospec(Minio, instance=True)
        client.bucket_exists.return_value = False
        storage_module.Storage(client).ensure_buckets()
        assert client.make_bucket.call_count == len(storage_module.BUCKETS)
        # No bucket policy may make any object anonymously readable.
        client.set_bucket_policy.assert_not_called()


# --- thumb URL is always presigned, never a bare /media/ path -----------------


class TestThumbUrlPresigned:
    def test_public_ready_thumb_is_presigned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        forbid_network(monkeypatch)
        url = videos_api._thumb_url(_ready_video(), real_storage())
        assert url is not None and not url.startswith("/media/")
        parts = urlsplit(url)
        assert parts.netloc == get_settings().s3_public_endpoint
        assert "X-Amz-Signature=" in parts.query
        assert "/thumbnails/thumbnails/x.jpg" in parts.path

    def test_private_thumb_is_presigned_not_none_not_media(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        forbid_network(monkeypatch)
        url = videos_api._thumb_url(_ready_video(is_private=True), real_storage())
        assert url is not None and not url.startswith("/media/")
        assert "X-Amz-Signature=" in urlsplit(url).query

    def test_no_thumbnail_key_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        forbid_network(monkeypatch)
        v = _ready_video(thumbnail_key=None)
        assert videos_api._thumb_url(v, real_storage()) is None


# --- API-level: non-public videos leak no thumb to anonymous ------------------


def _api_client(videos: list) -> TestClient:
    app = create_app()
    app.dependency_overrides[videos_api.get_video_repo] = lambda: FakeVideoRepo(videos)
    app.dependency_overrides[videos_api.get_feed_repo] = lambda: FakeFeedRepo(videos)
    app.dependency_overrides[auth_api.get_user_repo] = lambda: FakeUserRepo()
    return TestClient(app)


class TestApiThumbLeak:
    def test_hidden_video_card_404_for_anonymous(self) -> None:
        v = _video(status="hidden")
        with _api_client([v]) as c:
            r = c.get(f"/api/videos/{v.id}")
        assert r.status_code == 404
        assert "thumbnail_url" not in r.json() if r.status_code == 404 else True

    def test_private_video_card_404_for_anonymous(self) -> None:
        v = _video(is_private=True)
        with _api_client([v]) as c:
            r = c.get(f"/api/videos/{v.id}")
        assert r.status_code == 404

    def test_feed_item_thumbnail_is_presigned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        forbid_network(monkeypatch)
        v = _video(title="Clip")
        app = create_app()
        app.dependency_overrides[videos_api.get_video_repo] = lambda: FakeVideoRepo([v])
        app.dependency_overrides[videos_api.get_feed_repo] = lambda: FakeFeedRepo([v])
        app.dependency_overrides[auth_api.get_user_repo] = lambda: FakeUserRepo()
        app.dependency_overrides[videos_api.get_storage] = lambda: real_storage()
        with TestClient(app) as c:
            r = c.get("/api/feed")
        assert r.status_code == 200
        thumb = r.json()["items"][0]["thumbnail_url"]
        assert thumb and not thumb.startswith("/media/")
        assert "X-Amz-Signature=" in urlsplit(thumb).query


# --- pages: home card thumb is presigned, no /media/ --------------------------


class TestPagesThumbPresigned:
    def test_home_card_img_src_is_presigned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        forbid_network(monkeypatch)
        v = _video(title="Demo clip")
        app = create_app()
        app.dependency_overrides[videos_api.get_feed_repo] = lambda: FakeFeedRepo([v])
        app.dependency_overrides[videos_api.get_video_repo] = lambda: FakeVideoRepo([v])
        app.dependency_overrides[auth_api.get_user_repo] = lambda: FakeUserRepo()
        app.dependency_overrides[videos_api.get_storage] = lambda: real_storage()
        with TestClient(app) as c:
            r = c.get("/")
        assert r.status_code == 200
        assert "Demo clip" in r.text
        assert "/media/" not in r.text, "no anonymous /media/ thumb may be rendered"
        assert "X-Amz-Signature=" in r.text, "card thumb must be a presigned URL"


# --- UI placeholder without inline JS (CSP) -----------------------------------


class TestPlaceholderNoInlineJs:
    def test_no_template_uses_inline_onerror(self) -> None:
        for tpl in TEMPLATES.glob("*.html"):
            text = tpl.read_text(encoding="utf-8")
            assert "onerror=" not in text, f"{tpl.name}: inline onerror violates CSP"
            assert "onload=" not in text, f"{tpl.name}: inline onload violates CSP"

    def test_app_js_swaps_failed_thumb_for_static_placeholder(self) -> None:
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        assert 'addEventListener("error"' in js, "app.js must attach an error listener on thumbs"
        assert "/static/thumb-ph.svg" in js
        assert (STATIC / "thumb-ph.svg").is_file(), "placeholder asset must exist"

    def test_cards_template_has_placeholder_when_no_thumb(self) -> None:
        cards = (TEMPLATES / "_cards.html").read_text(encoding="utf-8")
        assert "thumb-ph" in cards
        assert "{% if c.thumb %}" in cards
