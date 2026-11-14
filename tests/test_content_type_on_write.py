"""Task 1 (red): every object write must carry an explicit content_type.

Without it S3 stores application/octet-stream and the browser DOWNLOADS the
video instead of playing it (real-stack report: presigned play_url returned
200/206 but Content-Type was octet-stream).

- API raw upload  -> Storage.put_stream(..., content_type=<mapped from ext>)
- worker transcode -> upload_file("videos", ..., content_type="video/mp4")
- worker thumbnail -> upload_file("thumbnails", ..., content_type="image/jpeg")
- Storage.put_stream / put_file must forward content_type to
  minio put_object / fput_object (autospec of the REAL Minio pins the signature).
"""

import asyncio
import os
import uuid
from pathlib import Path
from unittest.mock import create_autospec

from fastapi.testclient import TestClient
from minio import Minio

from app.api import auth as auth_api
from app.api import videos as videos_api
from app.core.storage import Storage, StorageObject
from app.main import create_app
from app.services import transcode
from app.services import videos as videos_svc

MP4_HEAD = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 48


def _called_content_type(call) -> str | None:
    """content_type position differs: put_object(bucket,key,data,length,ct,...)
    vs fput_object(bucket,key,path,ct,...)."""
    if "content_type" in call.kwargs:
        return call.kwargs["content_type"]
    args = call.args
    if len(args) > 4:  # put_object: index 4
        return args[4]
    if len(args) > 3:  # fput_object: index 3
        return args[3]
    return None


class TestStorageForwardsContentType:
    def test_put_stream_forwards_content_type(self, tmp_path) -> None:
        client = create_autospec(Minio, instance=True)
        st = Storage(client)
        p = tmp_path / "x.mp4"
        p.write_bytes(MP4_HEAD)
        st.put_stream("raw", "raw/a.mp4", str(p), content_type="video/mp4")
        assert _called_content_type(client.put_object.call_args) == "video/mp4"

    def test_put_file_forwards_content_type(self, tmp_path) -> None:
        client = create_autospec(Minio, instance=True)
        st = Storage(client)
        p = tmp_path / "t.jpg"
        p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
        st.put_file("thumbnails", "thumbnails/a.jpg", str(p), content_type="image/jpeg")
        assert _called_content_type(client.fput_object.call_args) == "image/jpeg"


class TestExtContentTypeMap:
    def test_every_allowed_extension_has_a_content_type(self) -> None:
        mapping = videos_svc.CONTENT_TYPE_BY_EXT
        assert set(videos_svc.ALLOWED_EXTENSIONS) <= set(mapping)
        assert mapping["mp4"] == "video/mp4"

    def test_content_type_for_ext_helper(self) -> None:
        assert videos_svc.content_type_for_ext("mp4") == "video/mp4"
        assert videos_svc.content_type_for_ext("webm") == "video/webm"


class _RecStorage:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, str | None]] = []

    def download_to(self, bucket: str, key: str, dest_path: str) -> None:
        Path(dest_path).write_bytes(MP4_HEAD)

    def upload_file(self, bucket: str, key: str, path: str, content_type: str | None = None) -> None:
        self.uploads.append((bucket, key, content_type))


class _RecRepo:
    def __init__(self, video_id: uuid.UUID) -> None:
        self.video = type("V", (), {"raw_key": f"raw/{video_id}.mp4"})()
        self.statuses: list = []

    def get(self, video_id):
        return self.video

    def set_status(self, video_id, status, **fields) -> None:
        self.statuses.append((status, fields))


class TestRunTranscodeContentTypes:
    def test_video_mp4_and_thumbnail_jpeg(self) -> None:
        vid = uuid.uuid4()
        storage = _RecStorage()
        repo = _RecRepo(vid)

        async def runner(args: list[str]):
            for i, a in enumerate(args):
                if a == "-y" and i + 1 < len(args):
                    Path(args[i + 1]).write_bytes(b"x" * 64)
            return True, ""

        async def probe(path: str):
            return transcode.ProbeInfo(ok=True, duration_seconds=12.0)

        asyncio.run(transcode.run_transcode(repo, storage, vid, runner, probe))
        by_bucket = {b: ct for b, _k, ct in storage.uploads}
        assert by_bucket.get("videos") == "video/mp4", storage.uploads
        assert by_bucket.get("thumbnails") == "image/jpeg", storage.uploads


class _ApiStorage:
    def __init__(self) -> None:
        self.put_calls: list[tuple[str, str, str | None]] = []

    def put_stream(self, bucket: str, key: str, path: str, content_type: str | None = None):
        self.put_calls.append((bucket, key, content_type))
        return StorageObject(bucket, key, os.stat(path).st_size)

    def presigned_get(self, bucket, key, ttl_seconds=None, response_headers=None):
        return f"http://example.com/{bucket}/{key}"


class _ApiVideoRepo:
    async def get(self, video_id):
        return None

    async def create(self, video):
        return video


class _ApiQueue:
    async def enqueue_transcode(self, video_id) -> None:
        pass


class _ApiUser:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.email = "u@example.com"
        self.channel_name = "U"
        self.role = "user"
        self.is_blocked = False


def test_upload_endpoint_puts_raw_with_mapped_content_type() -> None:
    app = create_app()
    storage = _ApiStorage()
    app.dependency_overrides[videos_api.get_video_repo] = lambda: _ApiVideoRepo()
    app.dependency_overrides[videos_api.get_storage] = lambda: storage
    app.dependency_overrides[videos_api.get_queue] = lambda: _ApiQueue()
    app.dependency_overrides[auth_api.get_current_user] = lambda: _ApiUser()
    with TestClient(app) as client:
        r = client.post(
            "/api/videos",
            files={"file": ("clip.mp4", MP4_HEAD, "video/mp4")},
            data={"title": "t", "description": ""},
        )
    assert r.status_code == 202, r.text
    assert storage.put_calls, "put_stream was not called"
    bucket, _key, ct = storage.put_calls[0]
    assert bucket == "raw"
    assert ct == "video/mp4", ct
