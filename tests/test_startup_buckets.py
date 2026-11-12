"""Startup wiring: the app must bootstrap its own storage buckets.

Review m02188: POST /api/videos failed on the real stack with
S3Error NoSuchBucket (bucket "raw") — ensure_buckets() existed but
nothing called it. The app now runs it from the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
from types import TracebackType
from unittest.mock import create_autospec

from minio import Minio

import app.main as main_module
from app.core import storage as storage_module


class _BucketProbe:
    """Test seam: records ensure-bucket calls made through the app."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        # Autospec keeps the REAL Minio signatures — see test_storage_real_signatures.
        self.client = create_autospec(Minio, instance=True)
        self.client.bucket_exists.return_value = True

    def wrap_storage(self, monkeypatch) -> None:
        probe_storage = storage_module.Storage(self.client)
        monkeypatch.setattr(storage_module, "get_storage", lambda: probe_storage)
        # main.py binds get_storage at import time — patch its binding too,
        # otherwise the lifespan reaches the real MinIO (review m02188 lesson:
        # Depends/module-level bindings need patching at the using module).
        monkeypatch.setattr(main_module, "get_storage", lambda: probe_storage)

    def __enter__(self) -> "_BucketProbe":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class TestStartupBuckets:
    def test_lifespan_creates_buckets_on_startup(self, monkeypatch) -> None:
        """Red first: lifespan must run ensure_buckets exactly once."""
        with _BucketProbe() as probe:
            probe.wrap_storage(monkeypatch)
            app = main_module.create_app()
            # Simulate the uvicorn startup/shutdown cycle.
            with asyncio.Runner() as runner:
                async def _cycle() -> None:
                    async with app.router.lifespan_context(app):
                        pass

                runner.run(_cycle())
        # bucket_exists probed for every known bucket; nothing created
        # because the probe reports them as existing.
        assert probe.client.bucket_exists.call_count == len(storage_module.BUCKETS)

    def test_lifespan_failure_logs_and_does_not_crash_app(self, monkeypatch, caplog) -> None:
        """Storage being down at boot must not kill the app: log, retry later."""
        with _BucketProbe() as probe:
            probe.wrap_storage(monkeypatch)
            probe.client.bucket_exists.side_effect = ConnectionError("silo down")

            app = main_module.create_app()
            with asyncio.Runner() as runner:
                async def _cycle() -> None:
                    async with app.router.lifespan_context(app):
                        pass

                runner.run(_cycle())
        assert any("ensure_buckets" in r.message for r in caplog.records)
