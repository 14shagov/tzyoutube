"""Presigned URLs must open in the BROWSER via nginx (m02086 task 4).

The presigning client signs against S3_PUBLIC_ENDPOINT (default
localhost, no port → 80) — not the internal silo:9000. Test asserts the
generated URL host and that signing needs no network call.
"""

from unittest.mock import create_autospec

from minio import Minio

import app.core.storage as storage_module
from app.core.storage import BUCKETS, Storage


class TestPresignsPublicHost:
    def test_get_storage_builds_two_clients(self, monkeypatch) -> None:
        monkeypatch.setattr(storage_module, "_storage", None)
        created: list[dict] = []

        class _MinioSpy:
            def __init__(self, endpoint, **kwargs):
                self.endpoint = endpoint
                self.kwargs = kwargs
                created.append({"endpoint": endpoint, "kwargs": kwargs})
                self._proto = create_autospec(Minio, instance=True)

            def __getattr__(self, name):
                return getattr(self._proto, name)

        import minio as minio_package

        monkeypatch.setattr(minio_package, "Minio", _MinioSpy)

        import app.core.config as config_module

        settings = config_module.Settings(
            _env_file=None,
            s3_endpoint="silo:9000",
            s3_public_endpoint="localhost",
        )
        monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
        # storage.get_settings is imported into the module namespace.
        monkeypatch.setattr(storage_module, "get_settings", lambda: settings)

        storage_module.get_storage()

        assert len(created) == 2
        assert created[0]["endpoint"] == "silo:9000"  # internal ops
        assert created[1]["endpoint"] == "localhost"  # presigning
        assert created[1]["kwargs"]["secure"] is False

    def test_presigned_url_uses_presign_client(self) -> None:
        internal = create_autospec(Minio, instance=True)
        presign = create_autospec(Minio, instance=True)
        presign.presigned_get_object.return_value = "http://localhost/videos/a.mp4?sig"
        storage = Storage(internal, presign_client=presign, presigned_ttl=60)
        url = storage.presigned_get(BUCKETS["videos"], "videos/a.mp4")
        assert url.startswith("http://localhost/")
        presign.presigned_get_object.assert_called_once()
        internal.presigned_get_object.assert_not_called()
