"""F7 red tests — HTML error pages (403/404/500) for browser routes, JSON kept
for API paths, and final no-external-URL guard over all templates/static."""

import re
from pathlib import Path

from fastapi.testclient import TestClient
from test_viewing_api import FakeStorage, _video

from app.api import auth as auth_api
from app.api import videos as videos_api
from app.main import create_app
from app.services.fakes import FakeUserRepo

# XML namespace identifiers (svg xmlns) are URIs by spec but never fetched —
# not external resources.
EXTERNAL = re.compile(
    r"https?://(?!localhost\b|127\.0\.0\.1\b|www\.w3\.org/2000/svg)", re.IGNORECASE
)


def _app(user=None, video=None):
    app = create_app()
    video = video or _video(user_id=user.id if user else uuid4(), title="clip")
    app.dependency_overrides[videos_api.get_video_repo] = lambda: _VideoRepo([video])
    app.dependency_overrides[videos_api.get_acl] = lambda: _Acl()
    app.dependency_overrides[videos_api.get_storage] = lambda: FakeStorage()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: FakeUserRepo()
    app.dependency_overrides[videos_api._optional_user] = lambda: user
    if user is not None:
        app.dependency_overrides[auth_api.get_current_user] = lambda: user
    return TestClient(app), video


class _VideoRepo:
    def __init__(self, videos):
        self.videos = videos

    async def get(self, video_id):
        for v in self.videos:
            if str(v.id) == str(video_id):
                return v
        return None


class _Acl:
    async def is_allowed(self, video_id, user_id):
        return False


def uuid4():
    import uuid

    return uuid.uuid4()


def test_404_watch_unknown_renders_error_page():
    c, _ = _app()
    r = c.get(f"/watch/{uuid4()}")
    assert r.status_code == 404
    assert 'data-testid="error-page"' in r.text
    assert "404" in r.text


def test_403_mod_for_plain_user_renders_error_page():
    from test_channels_api import _user

    user = _user(role="user", email="p@example.com", channel_name="plain")
    user.is_blocked = False
    app = create_app()
    from test_moderation_api import FakeReportRepo

    from app.api import moderation as moderation_api

    app.dependency_overrides[moderation_api.get_report_repo] = lambda: FakeReportRepo()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: FakeUserRepo()
    app.dependency_overrides[videos_api._optional_user] = lambda: user
    app.dependency_overrides[auth_api.get_current_user] = lambda: user
    r = TestClient(app).get("/mod")
    assert r.status_code == 403
    assert 'data-testid="error-page"' in r.text


def test_api_404_stays_json():
    c, _ = _app()
    r = c.get(f"/api/videos/{uuid4()}", headers={"accept": "application/json"})
    assert r.status_code == 404
    assert r.json() == {"detail": "not found"}
    assert r.headers["content-type"].startswith("application/json")


def test_unhandled_500_renders_error_page():
    app = create_app()
    from fastapi import APIRouter

    broken = APIRouter()

    @broken.get("/boom", include_in_schema=False)
    async def boom():
        raise RuntimeError("boom")

    app.include_router(broken)
    # Starlette's ServerErrorMiddleware always re-raises after responding,
    # so the TestClient must not propagate it.
    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    assert 'data-testid="error-page"' in r.text
    assert "500" in r.text


def test_no_external_urls_in_templates_and_static():
    root = Path(__file__).resolve().parent.parent / "src" / "app"
    offenders = []
    for d in ("templates", "static"):
        for f in (root / d).rglob("*"):
            if f.is_file():
                text = f.read_text(encoding="utf-8")
                for m in EXTERNAL.finditer(text):
                    offenders.append(f"{f.relative_to(root)}: ...{text[max(0, m.start()-40):m.end()+30]}...")
    assert not offenders, "external URLs found:\n" + "\n".join(offenders)


def test_no_inline_scripts_or_handlers_in_templates():
    root = Path(__file__).resolve().parent.parent / "src" / "app" / "templates"
    for f in root.rglob("*.html"):
        text = f.read_text(encoding="utf-8")
        for script in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", text, re.DOTALL | re.IGNORECASE):
            assert not script.strip(), f"{f.name}: inline <script> body"
        assert not re.search(r"\son\w+\s*=", text), f"{f.name}: inline on*= handler"
        assert "|safe" not in text and "| safe" not in text, f"{f.name}: |safe used"
