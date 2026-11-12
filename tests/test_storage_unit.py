"""Unit tests for the storage interface and the S3 implementation wrapper.

No real S3/Silo is touched here: the S3 client is faked (integration tests
with a live Silo live under the `integration` marker; see docs/manual-checks.md).
"""

from app.core.storage import BUCKETS, Storage, StorageObject


class FakeS3Client:
    """Duck-typed stand-in for minio.Minio: records calls, returns canned data."""

    def __init__(self) -> None:
        self.put_calls: list[tuple[str, str]] = []
        self.presigned_calls: list[tuple[str, str, int]] = []
        self.removed: list[tuple[str, str]] = []
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, bucket: str, key: str, data: bytes, **kwargs) -> None:
        self.put_calls.append((bucket, key))
        self.objects[(bucket, key)] = data

    def presigned_get_object(self, bucket: str, key: str, expires) -> str:
        seconds = int(expires.total_seconds())
        self.presigned_calls.append((bucket, key, seconds))
        return f"http://example.com/{bucket}/{key}?ttl={seconds}"

    def remove_object(self, bucket: str, key: str) -> None:
        self.removed.append((bucket, key))
        self.objects.pop((bucket, key), None)


def test_buckets_constants() -> None:
    # ADR-001: raw (private), videos (private), thumbnails (public-read).
    assert BUCKETS == {"raw": "raw", "videos": "videos", "thumbnails": "thumbnails"}


def test_storage_put_streams_to_raw_bucket() -> None:
    s3 = FakeS3Client()
    storage = Storage(s3, public_base_url="http://example.com")
    storage.put(BUCKETS["raw"], "abc", b"payload")
    assert s3.put_calls == [("raw", "abc")]
    assert s3.objects[("raw", "abc")] == b"payload"


def test_storage_presigned_get_uses_ttl() -> None:
    s3 = FakeS3Client()
    storage = Storage(s3, public_base_url="http://example.com", presigned_ttl=7200)
    url = storage.presigned_get(BUCKETS["videos"], "x.mp4")
    assert "ttl=7200" in url
    assert s3.presigned_calls == [("videos", "x.mp4", 7200)]


def test_storage_delete_removes_object() -> None:
    s3 = FakeS3Client()
    storage = Storage(s3, public_base_url="http://example.com")
    storage.put("raw", "k", b"v")
    storage.delete("raw", "k")
    assert ("raw", "k") not in s3.objects


def test_storage_object_dataclass() -> None:
    obj = StorageObject(bucket="raw", key="f", size=3)
    assert (obj.bucket, obj.key, obj.size) == ("raw", "f", 3)
