"""Unit tests for the transcode worker logic (subprocess calls are faked).

The real ffmpeg is never invoked in unit tests: run_transcode takes
async runner/probe callables that tests replace (C2: the real runner is
asyncio.create_subprocess_exec + kill on timeout). End-to-end transcode
with real ffmpeg+Silo runs under the `integration` marker
(docs/manual-checks.md).
"""

import uuid
from itertools import pairwise
from types import SimpleNamespace

import pytest

from app.services import transcode as tc


class FakeRepo:
    def __init__(self) -> None:
        self.videos: dict[uuid.UUID, object] = {}
        self.status_updates: list[tuple[uuid.UUID, str]] = []

    def get(self, video_id):
        return self.videos.get(video_id)

    def set_status(self, video_id, status, **fields):
        self.status_updates.append((video_id, status))
        video = self.videos[video_id]
        video.status = status
        for k, v in fields.items():
            setattr(video, k, v)


class FakeStorage:
    def __init__(self) -> None:
        self.tempfiles: dict[str, str] = {}

    def download_to(self, bucket: str, key: str, dest_path: str) -> None:
        with open(dest_path, "wb") as f:
            f.write(b"FAKERAW")

    def upload_file(self, bucket: str, key: str, path: str) -> None:
        self.tempfiles[f"{bucket}/{key}"] = path


class FakeRunner:
    """Replaces the subprocess ffmpeg invocation; creates output files like ffmpeg would."""

    def __init__(self, ok: bool = True, output: str = "err") -> None:
        self.ok = ok
        self.output = output
        self.calls: list[list[str]] = []

    def _create_outputs(self, args: list[str]) -> None:
        # Create every output target (args following each "-y").
        for flag, path in pairwise(args):
            if flag == "-y":
                with open(path, "wb") as f:
                    f.write(b"FAKEOUT")

    async def __call__(self, args: list[str]) -> tuple[bool, str]:
        self.calls.append(args)
        if self.ok:
            self._create_outputs(args)
        return (self.ok, self.output)


class FakeProbe:
    def __init__(self, duration: float = 12.5, ok: bool = True) -> None:
        self.duration = duration
        self.ok = ok
        self.calls: list[str] = []

    async def __call__(self, path: str) -> tc.ProbeInfo:
        self.calls.append(path)
        if self.ok:
            return tc.ProbeInfo(ok=True, duration_seconds=self.duration, container="mov,mp4",
                                video_codec="h264", audio_codec="aac")
        return tc.ProbeInfo(ok=False, reason="no video stream in output")


@pytest.fixture
def video_id() -> uuid.UUID:
    return uuid.uuid4()


def _setup(repo: FakeRepo, video_id: uuid.UUID) -> None:
    video = SimpleNamespace(
        id=video_id,
        raw_key=f"raw/{video_id}.mp4",
        status="processing",
        video_key=None,
        thumbnail_key=None,
        duration_seconds=0,
        failure_reason=None,
    )
    repo.videos[video_id] = video


async def test_run_transcode_success_sets_ready(video_id) -> None:
    repo, storage = FakeRepo(), FakeStorage()
    _setup(repo, video_id)
    runner = FakeRunner(ok=True)
    probe = FakeProbe(duration=12.5)
    out = await tc.run_transcode(repo, storage, video_id, runner=runner, probe=probe)
    assert out is not None
    assert repo.status_updates[-1][1] == "ready"
    video = repo.videos[video_id]
    assert video.video_key == f"videos/{video_id}.mp4"
    assert video.thumbnail_key == f"thumbnails/{video_id}.jpg"
    assert video.duration_seconds == 12


async def test_run_transcode_failure_sets_failed_with_reason(video_id) -> None:
    repo, storage = FakeRepo(), FakeStorage()
    _setup(repo, video_id)
    runner = FakeRunner(ok=False, output="ffmpeg exploded")
    out = await tc.run_transcode(repo, storage, video_id, runner=runner)
    assert out is None
    assert repo.status_updates[-1][1] == "failed"
    assert "ffmpeg exploded" in repo.videos[video_id].failure_reason


async def test_run_transcode_missing_video_noop(video_id) -> None:
    repo, storage = FakeRepo(), FakeStorage()
    out = await tc.run_transcode(repo, storage, video_id, runner=FakeRunner())
    assert out is None
    assert repo.status_updates == []


def test_ffmpeg_args_shape(video_id) -> None:
    # The ffmpeg command must use H.264+AAC, faststart, and stay capped at 480p.
    args = tc.build_ffmpeg_args("in.mp4", "out.mp4", "thumb.jpg")
    joined = " ".join(args)
    assert "libx264" in joined
    assert "aac" in joined
    assert "faststart" in joined
    assert "min(480,ih)" in joined


def test_parse_ffprobe_json_valid() -> None:
    payload = (
        '{"streams": ['
        '{"codec_type": "video", "codec_name": "h264"},'
        '{"codec_type": "audio", "codec_name": "aac"}'
        '], "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "83.45"}}'
    )
    info = tc.parse_ffprobe_json(payload)
    assert info.ok
    assert info.duration_seconds == pytest.approx(83.45)
    assert info.video_codec == "h264"
    assert info.audio_codec == "aac"
    assert "mp4" in (info.container or "")


def test_parse_ffprobe_json_rejects_garbage() -> None:
    assert tc.parse_ffprobe_json("not json").ok is False
    assert tc.parse_ffprobe_json('{"streams": [], "format": {"duration": "1.0"}}').ok is False
    no_dur = '{"streams": [{"codec_type": "video", "codec_name": "h264"}], "format": {}}'
    info = tc.parse_ffprobe_json(no_dur)
    assert info.ok is False
    assert info.reason == "no duration reported by ffprobe"
