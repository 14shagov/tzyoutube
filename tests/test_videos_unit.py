"""Unit tests for video upload validation and service logic.

Storage and queue are faked; HTTP wiring is tested in test_videos_api.py.
"""

import io

import pytest

from app.services import videos as videos_svc
from app.services.fakes import FakeUserRepo  # noqa: F401  (re-exported helpers)
from app.services.videos import (
    ALLOWED_EXTENSIONS,
    UploadRejected,
    detect_extension,
    validate_upload,
)


def _mp4(head: bytes = b"") -> bytes:
    # Minimal ftyp box: size (4 bytes BE) + 'ftyp' + brand.
    body = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    return head + (b"\x00\x00\x00\x24ftyp" + body[8:] if False else body)


class TestExtensionDetection:
    def test_mp4_magic_bytes(self) -> None:
        data = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isom"
        assert detect_extension(data) == "mp4"

    def test_webm_and_mk3d_are_treated_as_matroska(self) -> None:
        data = b"\x1a\x45\xdf\xa3" + b"\x00" * 16
        assert detect_extension(data) == "mkv"

    def test_mov_magic_bytes(self) -> None:
        # 'ftypqt' brand.
        data = b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  "
        assert detect_extension(data) == "mov"

    def test_avi_magic_bytes(self) -> None:
        data = b"RIFF\x00\x00\x00\x00AVI LIST"
        assert detect_extension(data) == "avi"

    def test_unknown_magic_returns_none(self) -> None:
        assert detect_extension(b"\x00" * 64) is None

    def test_short_chunk_returns_none(self) -> None:
        assert detect_extension(b"\x1a\x45") is None


class TestValidateUpload:
    def _args(self, filename: str, data: bytes, max_size: int) -> videos_svc.UploadArgs:
        return videos_svc.UploadArgs(
            filename=filename,
            data=io.BytesIO(data),
            declared_size=len(data),
            max_size=max_size,
        )

    def test_valid_mp4_passes(self) -> None:
        mp4 = _mp4()
        ext = validate_upload(self._args("clip.MP4", mp4, max_size=1024 * 1024))
        assert ext == "mp4"

    def test_extension_not_in_whitelist_rejected(self) -> None:
        mp4 = _mp4()
        with pytest.raises(UploadRejected, match="extension"):
            validate_upload(self._args("clip.exe", mp4, max_size=1024))

    def test_magic_mismatch_rejected(self) -> None:
        # .mp4 name but random payload.
        with pytest.raises(UploadRejected, match="content"):
            validate_upload(self._args("clip.mp4", b"\x00" * 64, max_size=1024))

    def test_size_over_limit_rejected(self) -> None:
        mp4 = _mp4()
        with pytest.raises(UploadRejected, match="size"):
            validate_upload(self._args("clip.mp4", mp4, max_size=8))

    def test_empty_file_rejected(self) -> None:
        with pytest.raises(UploadRejected):
            validate_upload(self._args("clip.mp4", b"", max_size=1024))

    def test_no_extension_rejected(self) -> None:
        with pytest.raises(UploadRejected, match="extension"):
            validate_upload(self._args("clip", b"\x00" * 64, max_size=1024))

    def test_allowed_extensions_list(self) -> None:
        assert ALLOWED_EXTENSIONS == {"mp4", "webm", "mkv", "mov", "avi"}
