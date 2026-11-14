"""Transcode domain: ffmpeg invocation shape, ffprobe validation, orchestration.

Pure logic lives here (testable without ffmpeg/ffprobe); the arq task in
worker/main calls run_transcode with real ASYNC callables (C2: the runner
is asyncio.create_subprocess_exec + kill on timeout, so a hung ffmpeg is
actually terminated instead of being abandoned by a cancelled thread).
"""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

# C3-hardened invocation: no stdin, only the file protocol (no http/rtsp/…
# on user-supplied uploads), bounded threads, never upscale below 480p
# (`min(480,ih)` keeps a 360p source at 360p instead of blurring it to 480p).
# ADR architecture.md: H.264 + AAC, faststart for streaming.
FFMPEG_ARGS_TEMPLATE = [
    "-nostdin",
    "-protocol_whitelist", "file",
    "-threads", "4",
    "-i", "{input}",
    "-vf", "scale=-2:'min(480,ih)'",
    "-c:v", "libx264",
    "-preset", "fast",
    "-c:a", "aac",
    "-movflags", "+faststart",
    "-y", "{output}",
]

# C3: explicit stream map (first video stream only) + same scale cap.
_THUMBNAIL_ARGS = [
    "-nostdin",
    "-protocol_whitelist", "file",
    "-ss", "1",
    "-i", "{input}",
    "-map", "0:v:0",
    "-frames:v", "1",
    "-vf", "scale=-2:'min(480,ih)'",
    "-y", "{thumb}",
]

# C2: the worker kills ffmpeg after this long. WorkerSettings.job_timeout
# is set higher so the runner's own timeout fires first and the failure is
# recorded with a reason instead of a bare arq cancellation.
FFMPEG_TIMEOUT_SECONDS = 600
PROBE_TIMEOUT_SECONDS = 60


class TranscodeRepo(Protocol):
    """Sync metadata access (the arq task adapts the async SQLAlchemy repo)."""

    def get(self, video_id: uuid.UUID) -> object | None: ...

    def set_status(self, video_id: uuid.UUID, status: str, **fields) -> None: ...


class TranscodeStorage(Protocol):
    def download_to(self, bucket: str, key: str, dest_path: str) -> None: ...

    def upload_file(self, bucket: str, key: str, path: str,
                    content_type: str | None = None) -> None: ...


AsyncRunner = Callable[[list[str]], Awaitable[tuple[bool, str]]]
AsyncProbe = Callable[[str], Awaitable["ProbeInfo"]]


@dataclass
class ProbeInfo:
    """C3: ffprobe verdict on the produced file — not just '32 bytes exist'."""

    ok: bool
    duration_seconds: float | None = None
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    reason: str | None = None


def parse_ffprobe_json(text: str) -> ProbeInfo:
    """Parse `ffprobe -print_format json` output into a ProbeInfo verdict.

    ok=True requires a video stream AND a positive duration; anything else
    is a rejection with a human-readable reason.
    """
    try:
        data = json.loads(text)
    except ValueError:
        return ProbeInfo(ok=False, reason="ffprobe output is not valid JSON")
    if not isinstance(data, dict):
        return ProbeInfo(ok=False, reason="ffprobe output is not a JSON object")

    streams = data.get("streams") or []
    video = next((s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}
    raw_duration = fmt.get("duration") if isinstance(fmt, dict) else None
    try:
        duration = float(raw_duration) if raw_duration is not None else None
    except (TypeError, ValueError):
        duration = None

    container = fmt.get("format_name") if isinstance(fmt, dict) else None
    if video is None:
        return ProbeInfo(ok=False, reason="no video stream in output", container=container)
    if duration is None or duration <= 0:
        return ProbeInfo(
            ok=False, reason="no duration reported by ffprobe", container=container,
            video_codec=video.get("codec_name"),
        )
    return ProbeInfo(
        ok=True,
        duration_seconds=duration,
        container=container,
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name") if audio else None,
    )


def build_ffmpeg_args(input_path: str, output_path: str, thumb_path: str) -> list[str]:
    """ffmpeg invocation: transcode (capped at 480p) + grab a thumbnail frame."""
    def _fill(template: list[str]) -> list[str]:
        return [
            a.format(input=input_path, output=output_path, thumb=thumb_path)
            for a in template
        ]

    return _fill(FFMPEG_ARGS_TEMPLATE) + _fill(_THUMBNAIL_ARGS)


def build_ffprobe_args(path: str) -> list[str]:
    """ffprobe invocation: JSON container/codec/duration report (C3)."""
    return [
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-protocol_whitelist", "file",
        "-i", path,
    ]


async def run_transcode(
    repo: TranscodeRepo,
    storage: TranscodeStorage,
    video_id: uuid.UUID,
    runner: AsyncRunner,
    probe: AsyncProbe | None = None,
) -> str | None:
    """Async orchestration: raw → videos/<id>.mp4 + thumbnails/<id>.jpg.

    Blocking repo/storage calls run in threads; ffmpeg/ffprobe run via the
    injected async callables. Returns the output mp4 path on success, None
    on failure. Any stage failure marks the video `failed` WITH
    failure_reason (C2); the produced file is validated by ffprobe before
    upload (C3) and its duration comes from ffprobe, not from parsing
    ffmpeg's stderr.
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

        duration: float | None = None
        if probe is not None:
            info = await probe(str(out_path))
            if not info.ok:
                await asyncio.to_thread(
                    _fail, f"output rejected by ffprobe: {info.reason or 'unknown'}"
                )
                return None
            duration = info.duration_seconds

        # Task 2: explicit content_type — a stored video must be served as
        # video/mp4 (else browsers download instead of playing) and the
        # thumbnail as image/jpeg.
        await asyncio.to_thread(
            storage.upload_file, "videos", f"videos/{video_id}.mp4", str(out_path),
            "video/mp4",
        )
        if thumb_path.exists():
            await asyncio.to_thread(
                storage.upload_file, "thumbnails", f"thumbnails/{video_id}.jpg",
                str(thumb_path), "image/jpeg",
            )

        await asyncio.to_thread(
            repo.set_status,
            video_id,
            "ready",
            video_key=f"videos/{video_id}.mp4",
            thumbnail_key=f"thumbnails/{video_id}.jpg",
            duration_seconds=int(duration) if duration is not None else 0,
        )
        return str(out_path)
