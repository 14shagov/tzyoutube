"""API tests for video upload: auth, validation, status codes.

Storage, queue, and DB are faked via dependency overrides. The current
user is injected by overriding get_current_user (no login flow needed).
"""

import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.api import videos as videos_api
from app.main import create_app

MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isom"


class FakeVideoRepo:
    def __init__(self) -> None:
        self.videos: dict[uuid.UUID, object] = {}

    async def create(self, video):
        self.videos[video.id] = video
        return video

    async def get(self, video_id):
        return self.videos.get(video_id)


class FakeStorage:
    def __init__(self) -> None:
        self.put_calls: list[tuple[str, str]] = []

    def put(self, bucket: str, key: str, data: bytes) -> object:
        self.put_calls.append((bucket, key))
        return object()

    def put_stream(self, bucket: str, key: str, path: str) -> object:
        from pathlib import Path

        self.put_calls.append((bucket, key))

        class _Obj:
            size = Path(path).stat().st_size

        return _Obj()

    def presigned_get(self, bucket: str, key: str, ttl_seconds=None) -> str:
        return f"http://example.com/{bucket}/{key}"

    def delete(self, bucket: str, key: str) -> None:
        pass


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[uuid.UUID] = []

    async def enqueue_transcode(self, video_id) -> None:
        self.enqueued.append(video_id)


class FakeUser:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.email = "uploader@example.com"
        self.channel_name = "Uploader"
        self.role = "user"
        self.is_blocked = False


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    video_repo = FakeVideoRepo()
    storage = FakeStorage()
    queue = FakeQueue()
    user = FakeUser()
    app.dependency_overrides[videos_api.get_video_repo] = lambda: video_repo
    app.dependency_overrides[videos_api.get_storage] = lambda: storage
    app.dependency_overrides[videos_api.get_queue] = lambda: queue
    app.dependency_overrides[auth_api.get_current_user] = lambda: user
    with TestClient(app) as c:
        yield c
    return None


def _upload(client: TestClient, filename: str = "clip.mp4", data: bytes = MP4):
    return client.post(
        "/api/videos",
        data={"title": "Test clip", "description": "desc"},
        files={"file": (filename, io.BytesIO(data), "video/mp4")},
    )


def test_upload_requires_authentication(client: TestClient) -> None:
    client.app.dependency_overrides.pop(auth_api.get_current_user)
    client.app.dependency_overrides[auth_api.get_current_user] = _guest_error
    r = _upload(client)
    assert r.status_code == 401


def _guest_error():
    from fastapi import HTTPException

    raise HTTPException(401, "not authenticated")


def test_upload_success_creates_processing_video(client: TestClient) -> None:
    r = _upload(client)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "processing"
    assert body["title"] == "Test clip"
    # Storage got the raw file and the queue got the transcode job.
    video_repo = client.app.dependency_overrides[videos_api.get_video_repo]()
    queue = client.app.dependency_overrides[videos_api.get_queue]()
    storage = client.app.dependency_overrides[videos_api.get_storage]()
    assert len(queue.enqueued) == 1
    assert storage.put_calls and storage.put_calls[0][0] == "raw"
    assert video_repo.videos


def test_upload_rejects_bad_extension(client: TestClient) -> None:
    r = _upload(client, filename="virus.exe")
    assert r.status_code == 415


def test_upload_rejects_magic_mismatch(client: TestClient) -> None:
    r = _upload(client, filename="fake.mp4", data=b"\x00" * 64)
    assert r.status_code == 415


def test_upload_rejects_oversize(client: TestClient) -> None:
    # max_upload_size in test settings is huge; simulate a tight limit by
    # overriding the settings dependency used by the endpoint.
    from app.core.config import Settings

    tight = Settings(_env_file=None, max_upload_size=8)
    client.app.dependency_overrides[videos_api.get_upload_settings] = lambda: tight
    r = _upload(client)
    assert r.status_code == 413


def test_upload_empty_file_rejected(client: TestClient) -> None:
    r = _upload(client, data=b"")
    assert r.status_code == 415
