"""Reproduction: real minio-py client signatures (review m02086).

Fakes in unit tests had a different put_object signature than the real
minio==7.2.20 client, so the missing `length` argument only exploded on
the real stack (500 on POST /api/videos). These tests pin Storage to
the REAL library signatures via create_autospec(Minio).
"""

import tempfile
from unittest.mock import call, create_autospec

import pytest
from minio import Minio

from app.core.storage import BUCKETS, Storage


@pytest.fixture()
def real_client():
    """Autospec'd Minio: calls with wrong signatures now FAIL in tests."""
    client = create_autospec(Minio, instance=True)
    client.presigned_get_object.return_value = "http://example.com/video"
    return client


class TestPutMatchesRealSignature:
    def test_put_passes_length_to_real_client(self, real_client) -> None:
        storage = Storage(real_client, presigned_ttl=60)
        obj = storage.put(BUCKETS["raw"], "raw/x.mp4", b"hello")
        # The real put_object requires `length` — autospec raises
        # TypeError if Storage omits it.
        assert obj.size == 5
        real_client.put_object.assert_called_once()

    def test_put_stream_passes_length_and_part_size(self, real_client) -> None:
        storage = Storage(real_client, presigned_ttl=60)
        # Write WITHOUT the file handle held open (Windows: an open
        # NamedTemporaryFile cannot be reopened by Storage).
        import os

        fd, path = tempfile.mkstemp(suffix=".mp4")
        try:
            os.write(fd, b"streamed-bytes")
            os.close(fd)
            size = os.stat(path).st_size
            obj = storage.put_stream(BUCKETS["raw"], "raw/y.mp4", path)
        finally:
            os.unlink(path)
        assert obj.size == size
        real_client.put_object.assert_called_once()

    def test_put_from_file_uses_fput(self, real_client) -> None:
        storage = Storage(real_client, presigned_ttl=60)
        with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
            tmp.write(b"file-bytes")
            tmp.flush()
            storage.put_file(BUCKETS["raw"], "raw/z.mp4", tmp.name)
        real_client.fput_object.assert_called_once()

    def test_presigned_get_returns_url(self, real_client) -> None:
        storage = Storage(real_client, presigned_ttl=60)
        url = storage.presigned_get(BUCKETS["videos"], "videos/z.mp4")
        assert url == "http://example.com/video"


class TestEnsureBuckets:
    def test_ensure_buckets_creates_missing(self, real_client) -> None:
        real_client.bucket_exists.return_value = False
        storage = Storage(real_client, presigned_ttl=60)
        storage.ensure_buckets()
        assert real_client.make_bucket.call_count == len(BUCKETS)
        # Sequence matters: check first, create only what is missing, one
        # probe per bucket in BUCKETS order.
        expected = []
        for bucket in BUCKETS.values():
            expected.append(call.bucket_exists(bucket))
            expected.append(call.make_bucket(bucket))
        assert real_client.method_calls == expected

    def test_ensure_buckets_skips_existing(self, real_client) -> None:
        real_client.bucket_exists.return_value = True
        storage = Storage(real_client, presigned_ttl=60)
        storage.ensure_buckets()
        real_client.make_bucket.assert_not_called()
        # Only the probes happened, in BUCKETS order, nothing else.
        assert real_client.method_calls == [
            call.bucket_exists(bucket) for bucket in BUCKETS.values()
        ]
