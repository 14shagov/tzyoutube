"""Unit tests for the transcode worker logic (ffmpeg calls are faked).

The real ffmpeg is never invoked in unit tests: run_transcode takes
runner/clock callables that tests replace. End-to-end transcode with real
ffmpeg+Silo runs under the `integration` marker (docs/manual-checks.md).
"""

import uuid

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
    """Replaces subprocess ffmpeg invocation; creates output files like ffmpeg would."""

    def __init__(self, ok: bool = True, duration: float = 12.5) -> None:
        self.ok = ok
        self.duration = duration
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> tuple[bool, str]:
        self.calls.append(args)
        if self.ok:
            # Create every output target (args following each "-y").
            from itertools import pairwise

            for flag, path in pairwise(args):
                if flag == "-y":
                    with open(path, "wb") as f:
                        f.write(b"FAKEOUT")
        return (self.ok, f"Duration: 00:00:{self.duration:05.2f}")


class FakeProbe:
    def __init__(self, duration: float = 12.5, ok: bool = True) -> None:
        self.duration = duration
        self.ok = ok
        self.calls: list[str] = []

    def __call__(self, path: str) -> float | None:
        self.calls.append(path)
        return self.duration if self.ok else None


@pytest.fixture
def video_id() -> uuid.UUID:
    return uuid.uuid4()


def _setup(repo: FakeRepo, video_id: uuid.UUID) -> None:
    from types import SimpleNamespace

    video = SimpleNamespace(
        id=video_id,
        raw_key=f"raw/{video_id}.mp4",
        status="processing",
        video_key=None,
        thumbnail_key=None,
        duration_seconds=0,
    )
    repo.videos[video_id] = video


def test_run_transcode_success_sets_ready(video_id) -> None:
    repo, storage = FakeRepo(), FakeStorage()
    _setup(repo, video_id)
    runner = FakeRunner(ok=True)
    probe = FakeProbe(duration=12.5)
    out = tc.run_transcode(repo, storage, video_id, runner=runner, probe=probe)
    assert out is not None
    assert repo.status_updates[-1][1] == "ready"
    video = repo.videos[video_id]
    assert video.video_key == f"videos/{video_id}.mp4"
    assert video.thumbnail_key == f"thumbnails/{video_id}.jpg"
    assert video.duration_seconds == 12


def test_run_transcode_failure_sets_failed(video_id) -> None:
    repo, storage = FakeRepo(), FakeStorage()
    _setup(repo, video_id)
    runner = FakeRunner(ok=False)
    out = tc.run_transcode(repo, storage, video_id, runner=runner)
    assert out is None
    assert repo.status_updates[-1][1] == "failed"


def test_run_transcode_missing_video_noop(video_id) -> None:
    repo, storage = FakeRepo(), FakeStorage()
    out = tc.run_transcode(repo, storage, video_id, runner=FakeRunner())
    assert out is None
    assert repo.status_updates == []


def test_ffmpeg_args_shape(video_id) -> None:
    # The ffmpeg command must scale to 480p, use H.264+AAC, faststart.
    args = tc.build_ffmpeg_args("in.mp4", "out.mp4", "thumb.jpg")
    joined = " ".join(args)
    assert "libx264" in joined
    assert "aac" in joined
    assert "faststart" in joined
    assert "480" in joined


def test_parse_duration_from_ffmpeg_output() -> None:
    line = "  Duration: 00:01:23.45, start: 0.000000, bitrate: 1000 kb/s"
    assert tc.parse_duration(line) == pytest.approx(83.45, abs=0.01)
    assert tc.parse_duration("no duration here") is None
