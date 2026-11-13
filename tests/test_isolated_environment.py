"""Self-checks: the conftest isolation patches are actually engaged.

If someone deletes the autouse fixtures, the suite silently reverts to
~30s per TestClient entry and cost-12 hashing (docs/status.md, profile
section). These tests fail loudly instead.
"""

from unittest.mock import NonCallableMock

import app.main as main_module
from app.services import auth


def test_lifespan_storage_is_a_stub_not_real_minio() -> None:
    storage = main_module.get_storage()
    assert isinstance(storage._s3, NonCallableMock), (
        "app.main.get_storage must point at an autospec Minio stub in tests; "
        "otherwise every `with TestClient(app)` waits on a real silo"
    )
    # Must be instant: buckets reported as existing, no network retries.
    storage.ensure_buckets()
    assert storage._s3.bucket_exists.call_count == 3


def test_bcrypt_cost_is_lowered_inside_tests() -> None:
    assert auth._BCRYPT_ROUNDS == 4, (
        "tests must hash at lowered cost; production guard lives in "
        "test_production_bcrypt_cost.py"
    )
