"""Object storage interface (ADR-001: Silo via standard S3 API).

The Storage class wraps an S3-compatible client (minio-py). Unit tests
inject a fake client; the concrete Silo connection is created lazily in
production wiring (app.core.storage.get_storage).
"""

import datetime as dt
from typing import Any, Protocol

from app.core.config import get_settings

# ADR-001: raw (private uploads), videos (private, transcoded),
# thumbnails (public-read, served by NGINX without signatures).
BUCKETS: dict[str, str] = {
    "raw": "raw",
    "videos": "videos",
    "thumbnails": "thumbnails",
}


class StorageObject:
    """Reference to a stored object (bucket + key + size)."""

    def __init__(self, bucket: str, key: str, size: int) -> None:
        self.bucket = bucket
        self.key = key
        self.size = size


class S3ClientProtocol(Protocol):
    """Subset of the minio-py API that Storage relies on."""

    def put_object(self, bucket: str, key: str, data: Any, **kwargs: Any) -> None: ...

    def presigned_get_object(self, bucket: str, key: str, expires: dt.timedelta) -> str: ...

    def remove_object(self, bucket: str, key: str) -> None: ...


class Storage:
    """Thin wrapper over an S3 client; no HTTP knowledge (ADR-005)."""

    def __init__(
        self,
        s3: S3ClientProtocol,
        public_base_url: str = "",
        presigned_ttl: int = 4 * 3600,
    ) -> None:
        self._s3 = s3
        self.public_base_url = public_base_url
        self.presigned_ttl = presigned_ttl

    def put(self, bucket: str, key: str, data: bytes) -> StorageObject:
        self._s3.put_object(bucket, key, data)
        return StorageObject(bucket, key, len(data))

    def presigned_get(self, bucket: str, key: str, ttl_seconds: int | None = None) -> str:
        ttl = dt.timedelta(seconds=ttl_seconds if ttl_seconds is not None else self.presigned_ttl)
        return self._s3.presigned_get_object(bucket, key, ttl)

    def delete(self, bucket: str, key: str) -> None:
        self._s3.remove_object(bucket, key)


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
        _storage = Storage(client, presigned_ttl=settings.presigned_ttl_seconds)
    return _storage
