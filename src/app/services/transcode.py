"""Transcode domain: ffmpeg invocation shape, parsing, orchestration.

Pure logic lives here (testable without ffmpeg); the arq task in
app/worker/main calls run_transcode with real callables. The function is
fully synchronous: the arq task wraps DB work in its own event loop.
"""

import re
import uuid
from collections.abc import Callable
from typing import Protocol

# ADR architecture.md: 480p H.264 + AAC, faststart for streaming.
FFMPEG_ARGS_TEMPLATE = [
    "-i", "{input}",
    "-vf", "scale=-2:480",
    "-c:v", "libx264",
    "-preset", "fast",
    "-c:a", "aac",
    "-movflags", "+faststart",
    "-y", "{output}",
]

_THUMBNAIL_ARGS = ["-ss", "1", "-i", "{input}", "-frames:v", "1", "-y", "{thumb}"]

_DURATION_RE = re.compile(r"Duration: (\d+):(\d+):(\d+)\.(\d+)")


class TranscodeRepo(Protocol):
    """Sync metadata access (the arq task adapts the async SQLAlchemy repo)."""

    def get(self, video_id: uuid.UUID) -> object | None: ...

    def set_status(self, video_id: uuid.UUID, status: str, **fields) -> None: ...


class TranscodeStorage(Protocol):
    def download_to(self, bucket: str, key: str, dest_path: str) -> None: ...

    def upload_file(self, bucket: str, key: str, path: str) -> None: ...


Runner = Callable[[list[str]], tuple[bool, str]]
Probe = Callable[[str], float | None]


def parse_duration(ffmpeg_output: str) -> float | None:
    """Extract duration in seconds from ffmpeg stderr output."""
    m = _DURATION_RE.search(ffmpeg_output)
    if not m:
        return None
    h, mi, s, frac = (int(g) for g in m.groups())
    return h * 3600 + mi * 60 + s + frac / 100


def build_ffmpeg_args(input_path: str, output_path: str, thumb_path: str) -> list[str]:
    """ffmpeg invocation: transcode to 480p MP4 + grab a thumbnail frame."""
    def _fill(template: list[str]) -> list[str]:
        return [
            a.format(input=input_path, output=output_path, thumb=thumb_path)
            for a in template
        ]

    return _fill(FFMPEG_ARGS_TEMPLATE) + _fill(_THUMBNAIL_ARGS)


def run_transcode(
    repo: TranscodeRepo,
    storage: TranscodeStorage,
    video_id: uuid.UUID,
    runner: Runner,
    probe: Probe | None = None,
) -> str | None:
    """Synchronous orchestration: raw → videos/<id>.mp4 + thumbnails/<id>.jpg.

    Returns the output mp4 path on success, None on failure. Statuses are
    written through the repo (ready / failed) per db-schema.md.
    """
    import tempfile
    from pathlib import Path

    video = repo.get(video_id)
    if video is None:
        return None

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        raw_path = tmp_dir / "input.raw"
        out_path = tmp_dir / "output.mp4"
        thumb_path = tmp_dir / "thumb.jpg"

        storage.download_to("raw", video.raw_key, str(raw_path))
        args = build_ffmpeg_args(str(raw_path), str(out_path), str(thumb_path))
        ok, _ffmpeg_output = runner(args)

        if not ok or not out_path.exists():
            repo.set_status(video_id, "failed")
            return None

        storage.upload_file("videos", f"videos/{video_id}.mp4", str(out_path))
        if thumb_path.exists():
            storage.upload_file("thumbnails", f"thumbnails/{video_id}.jpg", str(thumb_path))

        duration = None
        if probe is not None:
            duration = probe(str(out_path))
        repo.set_status(
            video_id,
            "ready",
            video_key=f"videos/{video_id}.mp4",
            thumbnail_key=f"thumbnails/{video_id}.jpg",
            duration_seconds=int(duration) if duration is not None else 0,
        )
        return str(out_path)
