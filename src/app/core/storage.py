"""Object storage interface (ADR-001: Silo via standard S3 API).

The Storage class wraps an S3-compatible client (minio-py 7.2.20).
Unit tests pin the wrapper to the REAL library signatures via
create_autospec(Minio) — a fake with an invented signature once let a
missing `length` argument reach production (review m02086).

Two clients are kept (ADR-003):
- internal (S3_ENDPOINT, e.g. silo:9000) for PUT/DELETE/stat;
- presigning client (S3_PUBLIC_ENDPOINT, no port → port 80 via nginx)
  only to sign browser-openable URLs; signing is offline (no network).
"""

import datetime as dt
import os
from typing import IO, Any, Protocol

from app.core.config import get_settings

# ADR-001: raw (private uploads), videos (private, transcoded),
# thumbnails (public-read, served by NGINX without signatures).
BUCKETS: dict[str, str] = {
    "raw": "raw",
    "videos": "videos",
    "thumbnails": "thumbnails",
}

# minio-py multipart threshold for streams of known length.
_PART_SIZE = 16 * 1024 * 1024


class StorageObject:
    """Reference to a stored object (bucket + key + size)."""

    def __init__(self, bucket: str, key: str, size: int) -> None:
        self.bucket = bucket
        self.key = key
        self.size = size


class S3ClientProtocol(Protocol):
    """Subset of the minio-py 7.2.20 API that Storage relies on."""

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: IO[Any],
        length: int,
        part_size: int = 0,
        **kwargs: Any,
    ) -> Any: ...

    def fput_object(self, bucket_name: str, object_name: str, file_path: str, **kwargs: Any) -> Any: ...

    def fget_object(self, bucket_name: str, object_name: str, file_path: str, **kwargs: Any) -> Any: ...

    def presigned_get_object(self, bucket_name: str, object_name: str, expires: dt.timedelta) -> str: ...

    def remove_object(self, bucket_name: str, object_name: str) -> None: ...

    def bucket_exists(self, bucket_name: str) -> bool: ...

    def make_bucket(self, bucket_name: str, **kwargs: Any) -> None: ...


class Storage:
    """Thin wrapper over an S3 client; no HTTP knowledge (ADR-005).

    minio-py is synchronous: every method here is blocking and callers
    on the web tier must run them in a threadpool.
    """

    def __init__(
        self,
        s3: S3ClientProtocol,
        presign_client: S3ClientProtocol | None = None,
        presigned_ttl: int = 4 * 3600,
    ) -> None:
        self._s3 = s3
        self._presign = presign_client or s3
        self.presigned_ttl = presigned_ttl

    # -- writes -----------------------------------------------------------

    def put(self, bucket: str, key: str, data: bytes) -> StorageObject:
        """Small in-memory payloads (tests, thumbnails)."""
        import io

        self._s3.put_object(bucket, key, io.BytesIO(data), len(data))
        return StorageObject(bucket, key, len(data))

    def put_stream(self, bucket: str, key: str, path: str) -> StorageObject:
        """Stream a file object by path; length known from the file."""
        size = os.stat(path).st_size
        with open(path, "rb") as fh:
            self._s3.put_object(bucket, key, fh, size, part_size=_PART_SIZE)
        return StorageObject(bucket, key, size)

    def put_file(self, bucket: str, key: str, path: str) -> StorageObject:
        """File upload via fput (multipart handled by the library)."""
        size = os.stat(path).st_size
        self._s3.fput_object(bucket, key, path)
        return StorageObject(bucket, key, size)

    # -- reads ------------------------------------------------------------

    def presigned_get(self, bucket: str, key: str, ttl_seconds: int | None = None) -> str:
        ttl = dt.timedelta(seconds=ttl_seconds if ttl_seconds is not None else self.presigned_ttl)
        return self._presign.presigned_get_object(bucket, key, ttl)

    def download_to(self, bucket: str, key: str, dest_path: str) -> None:
        self._s3.fget_object(bucket, key, dest_path)

    # -- lifecycle --------------------------------------------------------

    def delete(self, bucket: str, key: str) -> None:
        self._s3.remove_object(bucket, key)

    def ensure_buckets(self) -> None:
        """Create every known bucket that does not exist yet."""
        for bucket in BUCKETS.values():
            if not self._s3.bucket_exists(bucket):
                self._s3.make_bucket(bucket)


_storage: Storage | None = None


def get_storage() -> Storage:
    """Lazy singleton wired to real Silo endpoints from settings."""
    global _storage
    if _storage is None:
        from minio import Minio

        settings = get_settings()
        client = Minio(
            settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=settings.s3_secure,
        )
        # ADR-003: presign against the PUBLIC endpoint so the signed URL
        # host matches what the browser uses (nginx → silo). Signing is
        # a local HMAC computation — no network call.
        presign_client = Minio(
            settings.s3_public_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=False,  # public entry is plain HTTP on port 80
        )
        _storage = Storage(
            client,
            presign_client=presign_client,
            presigned_ttl=settings.presigned_ttl_seconds,
        )
    return _storage
