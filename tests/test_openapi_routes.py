"""3G guard: every route required by docs/requirements.md exists in
app.openapi() and has typed (Pydantic $ref) request/response bodies — no bare
dict payloads. Run against the real app (dependency overrides are irrelevant:
openapi() is built from signatures only)."""

import pytest

from app.main import create_app

# (method, path) — the full API surface required by FR-1..FR-12.
EXPECTED_ROUTES = {
    # auth (FR-1/FR-2)
    ("POST", "/auth/register"),
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
    ("GET", "/auth/me"),
    # feed / search (FR-4)
    ("GET", "/api/feed"),
    ("GET", "/api/search"),
    ("GET", "/api/feed/subscriptions"),   # 3B
    ("GET", "/api/subscriptions"),          # 3B
    # video card + viewing (FR-4/FR-5)
    ("GET", "/api/videos/{video_id}"),
    ("POST", "/api/videos/{video_id}/view"),
    ("POST", "/api/videos/{video_id}/reactions"),
    ("GET", "/api/videos/{video_id}/reactions"),  # F3: my_reaction for the watch page
    ("POST", "/api/videos/{video_id}/comments"),
    ("GET", "/api/videos/{video_id}/comments"),
    # upload + management (FR-6, 3C, 3F)
    ("POST", "/api/videos"),
    ("PATCH", "/api/videos/{video_id}"),
    ("DELETE", "/api/videos/{video_id}"),
    ("GET", "/api/videos/{video_id}/status"),
    ("GET", "/api/me/videos"),
    # ACL whitelist (FR-9, 3C)
    ("GET", "/api/videos/{video_id}/acl"),
    ("POST", "/api/videos/{video_id}/acl"),
    ("DELETE", "/api/videos/{video_id}/acl/{user_id}"),
    # channels (FR-3, 3A)
    ("GET", "/api/channels/{channel_id}"),
    ("GET", "/api/channels/{channel_id}/videos"),
    ("PATCH", "/api/channels/{channel_id}"),
    ("GET", "/api/channels/{channel_id}/playlists"),  # 3D
    ("POST", "/api/channels/{channel_id}/subscribe"),  # 3B
    ("DELETE", "/api/channels/{channel_id}/subscribe"),
    # playlists (3D)
    ("POST", "/api/playlists"),
    ("GET", "/api/playlists/{playlist_id}"),
    ("PATCH", "/api/playlists/{playlist_id}"),
    ("DELETE", "/api/playlists/{playlist_id}"),
    ("POST", "/api/playlists/{playlist_id}/items"),
    ("DELETE", "/api/playlists/{playlist_id}/items/{video_id}"),
    # moderation + admin (FR-7/FR-8, 3E)
    ("POST", "/api/videos/{video_id}/report"),
    ("GET", "/api/mod/reports"),
    ("POST", "/api/mod/reports/{report_id}/resolve"),
    ("PATCH", "/api/admin/users/{user_id}"),
    # infra
    ("GET", "/health"),
}


@pytest.fixture(scope="module")
def spec() -> dict:
    return create_app().openapi()


def test_all_expected_routes_exist(spec: dict) -> None:
    paths = spec["paths"]
    missing = [
        (m, p) for (m, p) in sorted(EXPECTED_ROUTES)
        if m.lower() not in paths.get(p, {})
    ]
    assert not missing, f"missing routes in OpenAPI: {missing}"


def test_request_bodies_are_typed_models(spec: dict) -> None:
    """No bare-dict bodies: JSON request bodies must reference a component
    schema. Multipart uploads (file + form fields) are exempt."""
    bad = []
    for method, path in sorted(EXPECTED_ROUTES):
        op = spec["paths"].get(path, {}).get(method.lower())
        if op is None:
            continue
        body = op.get("requestBody", {})
        for media, content in body.get("content", {}).items():
            if media.startswith("multipart/"):
                continue
            schema = content.get("schema", {})
            if "$ref" not in schema:
                bad.append((method, path, media, schema))
    assert not bad, f"untyped request bodies: {bad}"


def test_success_responses_are_typed_models(spec: dict) -> None:
    """2xx responses with a body must reference a component schema."""
    bad = []
    for method, path in sorted(EXPECTED_ROUTES):
        op = spec["paths"].get(path, {}).get(method.lower())
        if op is None:
            continue
        for code, resp in op.get("responses", {}).items():
            if not code.startswith("2"):
                continue
            for media, content in resp.get("content", {}).items():
                schema = content.get("schema", {})
                if schema and "$ref" not in schema and schema.get("type") == "object":
                    bad.append((method, path, code, media, schema))
    assert not bad, f"untyped 2xx responses (bare dict): {bad}"
