"""Review C2: arq job timeout must cover the ffmpeg run; any stage
failure/timeout must mark the video `failed` WITH a reason; the ffmpeg
subprocess must be KILLED on timeout (create_subprocess_exec + kill —
to_thread cannot kill the child); max_jobs pinned explicitly.

The subprocess layer is mocked; real ffmpeg runs only under the
`integration` marker (docs/manual-checks.md).
"""

import asyncio
import uuid
from itertools import pairwise
from types import SimpleNamespace

from app.services import transcode as tc
from worker import main as worker_main


class TestWorkerSettings:
    def test_job_timeout_covers_ffmpeg_run(self) -> None:
        # arq default job_timeout is 300s < ffmpeg 600s: jobs were cancelled
        # mid-transcode and the video stuck in `processing`.
        assert worker_main.WorkerSettings.job_timeout >= tc.FFMPEG_TIMEOUT_SECONDS

    def test_max_jobs_pinned_explicitly(self) -> None:
        # default 10 would spawn 10 ffmpeg processes on one worker container.
        assert worker_main.WorkerSettings.max_jobs == 2


class _FakeProc:
    def __init__(self, returncode: int = 0, err: bytes = b"", hang: bool = False) -> None:
        self.returncode = returncode
        self._err = err
        self._hang = hang
        self.killed = False
        self.cmd: tuple = ()

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(30)
        return (b"", self._err)

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


def _patch_exec(monkeypatch, proc: _FakeProc) -> None:
    async def fake_exec(*args, **kwargs):
        proc.cmd = args
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


class TestFfmpegRunner:
    def test_timeout_kills_subprocess(self, monkeypatch) -> None:
        proc = _FakeProc(hang=True)
        _patch_exec(monkeypatch, proc)
        monkeypatch.setattr(worker_main, "FFMPEG_TIMEOUT_SECONDS", 0.05)
        ok, output = asyncio.run(worker_main._ffmpeg_runner(["-i", "x"]))
        assert ok is False
        assert proc.killed, "ffmpeg subprocess must be killed on timeout"
        assert "timeout" in output

    def test_success_returns_stderr(self, monkeypatch) -> None:
        proc = _FakeProc(returncode=0, err=b"frame= 100")
        _patch_exec(monkeypatch, proc)
        ok, output = asyncio.run(worker_main._ffmpeg_runner(["-i", "x"]))
        assert ok is True
        assert "frame= 100" in output
        assert proc.cmd[0] == "ffmpeg"

    def test_nonzero_returncode_fails_with_output(self, monkeypatch) -> None:
        proc = _FakeProc(returncode=1, err=b"moov atom not found")
        _patch_exec(monkeypatch, proc)
        ok, output = asyncio.run(worker_main._ffmpeg_runner(["-i", "x"]))
        assert ok is False
        assert "moov" in output


class _Repo:
    def __init__(self) -> None:
        self.videos: dict[uuid.UUID, object] = {}
        self.updates: list[tuple] = []

    def get(self, video_id):
        return self.videos.get(video_id)

    def set_status(self, video_id, status, **fields):
        self.updates.append((video_id, status, fields))
        video = self.videos[video_id]
        video.status = status
        for k, v in fields.items():
            setattr(video, k, v)


class _Storage:
    def download_to(self, bucket: str, key: str, dest_path: str) -> None:
        with open(dest_path, "wb") as f:
            f.write(b"RAW")

    def upload_file(self, bucket: str, key: str, path: str,
                    content_type: str | None = None) -> None:
        pass


def _video(video_id: uuid.UUID):
    return SimpleNamespace(
        id=video_id,
        raw_key=f"raw/{video_id}.mp4",
        status="processing",
        video_key=None,
        thumbnail_key=None,
        duration_seconds=0,
        failure_reason=None,
    )


class _AsyncRunner:
    def __init__(self, ok: bool = True, output: str = "err") -> None:
        self.ok = ok
        self.output = output
        self.calls: list[list[str]] = []

    def _create_outputs(self, args: list[str]) -> None:
        for flag, path in pairwise(args):
            if flag == "-y":
                with open(path, "wb") as f:
                    f.write(b"OUT")

    async def __call__(self, args: list[str]):
        self.calls.append(args)
        if self.ok:
            self._create_outputs(args)
        return self.ok, self.output


class TestRunTranscodeFailureReason:
    async def test_runner_failure_marks_failed_with_reason(self) -> None:
        vid = uuid.uuid4()
        repo = _Repo()
        repo.videos[vid] = _video(vid)
        out = await tc.run_transcode(
            repo, _Storage(), vid, runner=_AsyncRunner(ok=False, output="ffmpeg exploded")
        )
        assert out is None
        _, status, fields = repo.updates[-1]
        assert status == "failed"
        assert "ffmpeg exploded" in fields.get("failure_reason", "")

    async def test_timeout_output_becomes_failure_reason(self) -> None:
        vid = uuid.uuid4()
        repo = _Repo()
        repo.videos[vid] = _video(vid)
        out = await tc.run_transcode(
            repo,
            _Storage(),
            vid,
            runner=_AsyncRunner(ok=False, output="ffmpeg killed after 600s timeout"),
        )
        assert out is None
        _, status, fields = repo.updates[-1]
        assert status == "failed"
        assert "600s timeout" in fields["failure_reason"]

    async def test_success_marks_ready_with_duration(self) -> None:
        vid = uuid.uuid4()
        repo = _Repo()
        repo.videos[vid] = _video(vid)

        async def probe(path: str):
            return tc.ProbeInfo(ok=True, duration_seconds=12.5, video_codec="h264")

        out = await tc.run_transcode(repo, _Storage(), vid, runner=_AsyncRunner(), probe=probe)
        assert out is not None
        _, status, fields = repo.updates[-1]
        assert status == "ready"
        assert fields["duration_seconds"] == 12
        assert fields["video_key"] == f"videos/{vid}.mp4"
        assert fields["thumbnail_key"] == f"thumbnails/{vid}.jpg"

    async def test_missing_video_is_noop(self) -> None:
        repo = _Repo()
        out = await tc.run_transcode(repo, _Storage(), uuid.uuid4(), runner=_AsyncRunner())
        assert out is None
        assert repo.updates == []
