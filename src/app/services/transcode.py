"""Transcode domain: ffmpeg invocation shape, parsing, orchestration.

Pure logic lives here (testable without ffmpeg); the arq task in
worker/main calls run_transcode with real ASYNC callables (C2: the runner
is asyncio.create_subprocess_exec + kill on timeout, so a hung ffmpeg is
actually terminated instead of being abandoned by a cancelled thread).
"""

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable
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

# C2: the worker kills ffmpeg after this long. WorkerSettings.job_timeout
# is set higher so the runner's own timeout fires first and the failure is
# recorded with a reason instead of a bare arq cancellation.
FFMPEG_TIMEOUT_SECONDS = 600
PROBE_TIMEOUT_SECONDS = 60

_DURATION_RE = re.compile(r"Duration: (\d+):(\d+):(\d+)\.(\d+)")


class TranscodeRepo(Protocol):
    """Sync metadata access (the arq task adapts the async SQLAlchemy repo)."""

    def get(self, video_id: uuid.UUID) -> object | None: ...

    def set_status(self, video_id: uuid.UUID, status: str, **fields) -> None: ...


class TranscodeStorage(Protocol):
    def download_to(self, bucket: str, key: str, dest_path: str) -> None: ...

    def upload_file(self, bucket: str, key: str, path: str) -> None: ...


AsyncRunner = Callable[[list[str]], Awaitable[tuple[bool, str]]]
AsyncProbe = Callable[[str], Awaitable[float | None]]


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


def build_probe_args(path: str) -> list[str]:
    """Duration probe: `ffmpeg -i` prints container info on stderr.

    C3 replaces this with ffprobe JSON (container/codecs/duration).
    """
    return ["-i", path]


async def run_transcode(
    repo: TranscodeRepo,
    storage: TranscodeStorage,
    video_id: uuid.UUID,
    runner: AsyncRunner,
    probe: AsyncProbe | None = None,
) -> str | None:
    """Async orchestration: raw → videos/<id>.mp4 + thumbnails/<id>.jpg.

    Blocking repo/storage calls run in threads; ffmpeg/probe run via the
    injected async callables. Returns the output mp4 path on success, None
    on failure. Any stage failure marks the video `failed` WITH
    failure_reason (C2) — previously a bare "failed" left no diagnostics.
    """
    import tempfile
    from pathlib import Path

    video = await asyncio.to_thread(repo.get, video_id)
    if video is None:
        return None

    def _fail(reason: str) -> None:
        repo.set_status(video_id, "failed", failure_reason=reason[-500:])

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        raw_path = tmp_dir / "input.raw"
        out_path = tmp_dir / "output.mp4"
        thumb_path = tmp_dir / "thumb.jpg"

        try:
            await asyncio.to_thread(storage.download_to, "raw", video.raw_key, str(raw_path))
        except Exception as exc:  # noqa: BLE001 — any S3/IO failure means the job failed
            await asyncio.to_thread(_fail, f"download failed: {exc}")
            return None

        args = build_ffmpeg_args(str(raw_path), str(out_path), str(thumb_path))
        ok, ffmpeg_output = await runner(args)

        if not ok or not out_path.exists():
            await asyncio.to_thread(_fail, ffmpeg_output or "ffmpeg failed")
            return None

        await asyncio.to_thread(
            storage.upload_file, "videos", f"videos/{video_id}.mp4", str(out_path)
        )
        if thumb_path.exists():
            await asyncio.to_thread(
                storage.upload_file, "thumbnails", f"thumbnails/{video_id}.jpg", str(thumb_path)
            )

        duration = None
        if probe is not None:
            duration = await probe(str(out_path))
        await asyncio.to_thread(
            repo.set_status,
            video_id,
            "ready",
            video_key=f"videos/{video_id}.mp4",
            thumbnail_key=f"thumbnails/{video_id}.jpg",
            duration_seconds=int(duration) if duration is not None else 0,
        )
        return str(out_path)
