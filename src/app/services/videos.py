"""Video domain: upload validation, metadata, statuses.

Services must not know about HTTP (ADR-005). Storage and queue access is
abstracted behind protocols so unit tests can fake them.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import BinaryIO, Protocol

from app.models import Video


class UploadRejected(Exception):
    """Uploaded file failed validation (bad type, size, or content)."""


ALLOWED_EXTENSIONS = {"mp4", "webm", "mkv", "mov", "avi"}

# How many leading bytes are needed for magic-byte detection.
SNIFF_LEN = 32


class VideoStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    HIDDEN = "hidden"
    DELETED = "deleted"


@dataclass(slots=True)
class UploadArgs:
    """Everything needed to validate one uploaded file."""

    filename: str
    data: BinaryIO
    declared_size: int
    max_size: int


def detect_extension(data: bytes) -> str | None:
    """Return the canonical extension for known magic bytes, else None."""
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand.startswith(b"qt"):
            return "mov"
        return "mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":  # EBML → Matroska family.
        return "mkv"
    if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return "avi"
    return None


def validate_upload(args: UploadArgs) -> str:
    """Validate filename extension, magic bytes, and size; return extension.

    Raises UploadRejected with a short reason code in the message
    (extension / content / size / empty).
    """
    name = args.filename or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadRejected("extension not allowed")

    if args.declared_size <= 0:
        raise UploadRejected("empty file")
    if args.declared_size > args.max_size:
        raise UploadRejected("size over limit")

    head = args.data.read(SNIFF_LEN)
    args.data.seek(0)
    magic = detect_extension(head)
    if magic is None:
        raise UploadRejected("content does not match any known video type")
    # Matroska container covers both .mkv and .webm declared extensions;
    # mp4/mov brands overlap too — accept when families match.
    if magic != ext and not {magic, ext} <= {"mkv", "webm", "mp4", "mov"}:
        raise UploadRejected("content does not match extension")
    return ext


class VideoRepo(Protocol):
    """Storage interface for video metadata."""

    async def create(self, video: Video) -> Video: ...

    async def get(self, video_id: uuid.UUID) -> Video | None: ...


class RawStorage(Protocol):
    """Minimal storage interface needed by the upload flow."""

    def put(self, bucket: str, key: str, data: bytes) -> object: ...


class TranscodeQueue(Protocol):
    """Queue abstraction (arq in production; faked in tests)."""

    async def enqueue_transcode(self, video_id: uuid.UUID) -> None: ...


def build_raw_key(video_id: uuid.UUID, ext: str) -> str:
    return f"raw/{video_id}.{ext}"


def new_video(user_id: uuid.UUID, title: str, description: str, raw_key: str,
              size_bytes: int, is_private: bool = False) -> Video:
    """Create a Video row in the processing state (transcoding pending)."""
    return Video(
        user_id=user_id,
        title=title,
        description=description,
        raw_key=raw_key,
        size_bytes=size_bytes,
        is_private=is_private,
        status=VideoStatus.PROCESSING,
    )
