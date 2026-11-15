"""Soft-delete cleanup (FR-10): remove the S3 objects, then the DB row.

Runs in the worker as the ``delete_video`` job. Transient S3/DB errors are
retried with linear backoff; if every attempt fails the row is kept (status
stays ``deleted``) and the job raising lets arq retry later — the API
already answered 204 at soft-delete time.
"""

import time
import uuid
from typing import Protocol

from app.core.storage import BUCKETS


class CleanupRepo(Protocol):
    def get(self, video_id: uuid.UUID): ...

    def delete_video(self, video_id: uuid.UUID) -> None: ...


class CleanupStorage(Protocol):
    def delete(self, bucket: str, key: str) -> None: ...


def _object_keys(video) -> list[tuple[str, str]]:
    """(bucket, key) pairs of every stored object of the video."""
    pairs = [
        (BUCKETS["raw"], video.raw_key),
        (BUCKETS["videos"], video.video_key),
        (BUCKETS["thumbnails"], video.thumbnail_key),
    ]
    return [(bucket, key) for bucket, key in pairs if key]


def delete_video_objects(
    repo: CleanupRepo,
    storage: CleanupStorage,
    video_id: uuid.UUID,
    *,
    attempts: int = 3,
    backoff: float = 2.0,
) -> None:
    """Delete raw/transcoded/thumbnail objects, then hard-delete the row.

    Raises RuntimeError after ``attempts`` failures so the queue job fails
    visibly and can be retried; the video row is only removed once every
    object is gone.
    """
    video = repo.get(video_id)
    if video is None:
        return
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            for bucket, key in _object_keys(video):
                storage.delete(bucket, key)
            repo.delete_video(video_id)
            return
        except Exception as exc:  # noqa: BLE001 — retry any S3/DB error
            last_exc = exc
            if attempt < attempts:
                time.sleep(backoff * attempt)
    raise RuntimeError(
        f"delete_video_objects failed after {attempts} attempts: {last_exc}"
    )
