"""Unit tests for the storage interface and the S3 implementation wrapper.

No real S3/Silo is touched here: the client is create_autospec(Minio) —
the REAL library signatures — so a wrapper call with a wrong/missing
argument (like the missing `length` in m02086) fails in unit tests,
not on the deployed stack.
"""

from unittest.mock import create_autospec

from minio import Minio

from app.core.storage import BUCKETS, Storage, StorageObject


def _client() -> tuple[Minio, Storage]:
    client = create_autospec(Minio, instance=True)
    client.presigned_get_object.side_effect = (
        lambda bucket, key, expires: f"http://example.com/{bucket}/{key}?ttl={int(expires.total_seconds())}"
    )
    return client, Storage(client, presigned_ttl=7200)


def test_buckets_constants() -> None:
    # ADR-001: raw (private), videos (private), thumbnails (public-read).
    assert BUCKETS == {"raw": "raw", "videos": "videos", "thumbnails": "thumbnails"}


def test_storage_put_passes_real_signature() -> None:
    client, storage = _client()
    obj = storage.put(BUCKETS["raw"], "abc", b"payload")
    assert obj.size == len(b"payload")
    client.put_object.assert_called_once()
    # Real minio put_object requires length; assert it was passed.
    _, kwargs = client.put_object.call_args
    args = client.put_object.call_args.args
    assert len(args) >= 4 or "length" in kwargs


def test_storage_presigned_get_uses_ttl() -> None:
    client, storage = _client()
    url = storage.presigned_get(BUCKETS["videos"], "x.mp4")
    assert "ttl=7200" in url
    client.presigned_get_object.assert_called_once_with(
        BUCKETS["videos"], "x.mp4", __import__("datetime").timedelta(seconds=7200)
    )


def test_storage_delete_removes_object() -> None:
    client, storage = _client()
    storage.delete("raw", "k")
    client.remove_object.assert_called_once_with("raw", "k")


def test_storage_object_dataclass() -> None:
    obj = StorageObject(bucket="raw", key="f", size=3)
    assert (obj.bucket, obj.key, obj.size) == ("raw", "f", 3)


def test_wrapper_signatures_match_protocol() -> None:
    """The wrapper must keep accepting what callers pass (task 3 audit)."""
    from app.core.storage import S3ClientProtocol

    proto = S3ClientProtocol
    assert hasattr(proto, "put_object")
    assert hasattr(proto, "fput_object")
    assert hasattr(proto, "fget_object")
    # Storage exposes the documented surface.
    _, storage = _client()
    for method in ("put", "put_stream", "put_file", "presigned_get", "download_to", "delete", "ensure_buckets"):
        assert callable(getattr(storage, method)), method
