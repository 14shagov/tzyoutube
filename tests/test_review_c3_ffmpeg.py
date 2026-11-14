"""C3: ffmpeg command hardening + ffprobe-based output validation (red-first).

Covers: -nostdin, -protocol_whitelist file (no external protocols on
user uploads), bounded -threads, scale cap `min(480,ih)` (never upscale a
<480p source), thumbnail with explicit -map 0:v:0, ffprobe JSON probe with
container/codec/duration validation before upload, duration taken from
ffprobe (not ffmpeg stderr).
"""

import json
import uuid
from types import SimpleNamespace

from app.services import transcode as tc


class TestCommandBuilding:
    def test_nostdin_and_protocol_whitelist(self):
        args = tc.build_ffmpeg_args("in.raw", "out.mp4", "thumb.jpg")
        assert "-nostdin" in args
        i = args.index("-protocol_whitelist")
        assert args[i + 1] == "file"
        # exactly two whitelists (main transcode + thumbnail leg), both file-only
        assert args.count("-protocol_whitelist") == 2

    def test_no_external_protocols_anywhere(self):
        args = tc.build_ffmpeg_args("in.raw", "out.mp4", "thumb.jpg")
        forbidden = {"http", "https", "rtsp", "rtmp", "tcp", "udp", "concat", "pipe"}
        for idx, flag in enumerate(args):
            if flag == "-protocol_whitelist":
                assert args[idx + 1] not in forbidden
        joined = " ".join(args)
        assert "http" not in joined

    def test_threads_bounded(self):
        args = tc.build_ffmpeg_args("in.raw", "out.mp4", "thumb.jpg")
        i = args.index("-threads")
        assert 0 < int(args[i + 1]) <= 8

    def test_scale_never_upscales_below_480p(self):
        args = tc.build_ffmpeg_args("in.raw", "out.mp4", "thumb.jpg")
        scales = [a for a in args if a.startswith("scale=")]
        assert scales, "expected at least one scale filter"
        for s in scales:
            assert "min(480,ih)" in s
            assert "480)" not in s.replace("min(480,ih)", "")  # no hard 480 target

    def test_thumbnail_maps_first_video_stream(self):
        args = tc.build_ffmpeg_args("in.raw", "out.mp4", "thumb.jpg")
        i = args.index("-map")
        assert args[i + 1] == "0:v:0"

    def test_ffprobe_args_are_json_and_file_only(self):
        args = tc.build_ffprobe_args("out.mp4")
        assert "json" in args
        i = args.index("-protocol_whitelist")
        assert args[i + 1] == "file"
        assert args[-2:] == ["-i", "out.mp4"]


class TestFfprobeParsing:
    def test_valid_report(self):
        payload = json.dumps({
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"format_name": "mov,mp4", "duration": "12.5"},
        })
        info = tc.parse_ffprobe_json(payload)
        assert info.ok and info.duration_seconds == 12.5
        assert info.video_codec == "h264" and info.audio_codec == "aac"

    def test_rejects_non_json(self):
        assert tc.parse_ffprobe_json("moov atom garbage").ok is False

    def test_rejects_no_video_stream(self):
        payload = json.dumps({
            "streams": [{"codec_type": "audio", "codec_name": "aac"}],
            "format": {"duration": "5.0"},
        })
        info = tc.parse_ffprobe_json(payload)
        assert not info.ok and "video" in (info.reason or "")

    def test_rejects_zero_duration(self):
        payload = json.dumps({
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
            "format": {"duration": "0.0"},
        })
        assert tc.parse_ffprobe_json(payload).ok is False


class _Repo:
    def __init__(self) -> None:
        self.videos = {}
        self.updates = []

    def get(self, vid):
        return self.videos.get(vid)

    def set_status(self, vid, status, **fields):
        self.updates.append((vid, status, fields))


class _Storage:
    def __init__(self) -> None:
        self.uploads = []

    def download_to(self, bucket, key, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"RAW")

    def upload_file(self, bucket, key, path):
        self.uploads.append((bucket, key))


class _Runner:
    def __init__(self) -> None:
        self.calls = []

    def _create_outputs(self, args):
        from itertools import pairwise
        for flag, path in pairwise(args):
            if flag == "-y":
                with open(path, "wb") as f:
                    f.write(b"OUT" * 100)

    async def __call__(self, args):
        self.calls.append(args)
        self._create_outputs(args)
        return True, ""


def _video(vid):
    return SimpleNamespace(id=vid, raw_key=f"raw/{vid}", status="processing",
                           video_key=None, thumbnail_key=None, duration_seconds=0,
                           failure_reason=None)


class TestRunTranscodeProbeGate:
    async def test_probe_rejection_fails_and_uploads_nothing(self):
        vid = uuid.uuid4()
        repo = _Repo()
        repo.videos[vid] = _video(vid)
        storage = _Storage()

        async def bad_probe(path: str):
            return tc.ProbeInfo(ok=False, reason="no video stream in output")

        out = await tc.run_transcode(repo, storage, vid, runner=_Runner(), probe=bad_probe)
        assert out is None
        _, status, fields = repo.updates[-1]
        assert status == "failed"
        assert "ffprobe" in fields["failure_reason"]
        assert "no video stream" in fields["failure_reason"]
        assert storage.uploads == []

    async def test_success_uses_ffprobe_duration(self):
        vid = uuid.uuid4()
        repo = _Repo()
        repo.videos[vid] = _video(vid)
        storage = _Storage()

        async def good_probe(path: str):
            return tc.ProbeInfo(ok=True, duration_seconds=42.7, video_codec="h264")

        out = await tc.run_transcode(repo, storage, vid, runner=_Runner(), probe=good_probe)
        assert out is not None
        _, status, fields = repo.updates[-1]
        assert status == "ready"
        assert fields["duration_seconds"] == 42
        assert ("videos", f"videos/{vid}.mp4") in storage.uploads
