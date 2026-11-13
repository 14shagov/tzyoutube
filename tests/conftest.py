"""Shared fixtures: fast, network-free unit runs.

Why this exists: every `with TestClient(app)` enters the app lifespan,
which calls ensure_buckets() against a real silo endpoint. Outside
Docker that means ~30s of connection retries per test entry and a
28-minute full run (docs/status.md, profile section). Two autouse
fixtures keep runs fast and hermetic:

- _storage_stub: app.main.get_storage -> autospec(Minio) wrapper;
- _fast_bcrypt: cost 12 -> 4 inside tests only.

Both are guarded: test_isolated_environment.py asserts they are
engaged; test_production_bcrypt_cost.py guards the real default.
"""

from unittest.mock import create_autospec

import pytest
from minio import Minio

import app.main as main_module
import app.services.auth as auth_module
from app.core import storage as storage_module


@pytest.fixture(autouse=True)
def _storage_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the lifespan's get_storage at an offline autospec stub.

    Only the app.main binding is patched: the lifespan imports it at
    module import (m02188 lesson). storage_module.get_storage stays real
    so test_presigned_public_host keeps verifying actual client wiring.
    """
    client = create_autospec(Minio, instance=True)
    client.bucket_exists.return_value = True
    stub = storage_module.Storage(client)
    monkeypatch.setattr(main_module, "get_storage", lambda: stub)


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hash at cost 4 in tests: same algorithm, faster per register/login."""
    monkeypatch.setattr(auth_module, "_BCRYPT_ROUNDS", 4)
