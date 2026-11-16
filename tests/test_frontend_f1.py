"""F1 (owner m02359): base template, header, sidebar, theme, home feed, search.

Guards: templates/static contain no external URLs, no inline <script> or
on*= handlers, no |safe; nginx ships CSP script-src 'self'; user text is
escaped (XSS); static files are served; empty state renders.
Red before the pages module exists.
"""

import re
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from test_viewing_api import FakeFeedRepo, FakeVideoRepo, _video

from app.api import auth as auth_api
from app.api import videos as videos_api
from app.main import create_app
from app.services.fakes import FakeUserRepo

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "src" / "app" / "templates"
STATIC = ROOT / "src" / "app" / "static"


def _app(videos: list) -> TestClient:
    app = create_app()
    app.dependency_overrides[videos_api.get_feed_repo] = lambda: FakeFeedRepo(videos)
    app.dependency_overrides[videos_api.get_video_repo] = lambda: FakeVideoRepo(videos)
    app.dependency_overrides[auth_api.get_user_repo] = lambda: FakeUserRepo()
    return TestClient(app)


def test_templates_exist() -> None:
    assert (TEMPLATES / "base.html").is_file()
    assert (STATIC / "app.css").is_file()
    assert (STATIC / "app.js").is_file()
    assert (STATIC / "theme.js").is_file()


def test_home_guest_200_with_chrome_and_card() -> None:
    v = _video(title="Demo clip")
    with _app([v]) as c:
        r = c.get("/")
    assert r.status_code == 200
    body = r.text
    assert "TZ Video" in body                      # logo/brand
    assert "Главная" in body and "Подписки" in body  # sidebar
    assert "Demo clip" in body                     # card title
    assert f'href="/watch/{v.id}"' in body         # card links to watch page
    assert "/static/app.css" in body               # stylesheet linked
    assert 'src="/static/theme.js"' in body        # theme script external
    assert 'src="/static/app.js"' in body          # main js external


def test_home_guest_sees_login_button() -> None:
    with _app([]) as c:
        r = c.get("/")
    assert r.status_code == 200
    assert "Войти" in r.text


def test_search_page_renders_query_results() -> None:
    v = _video(title="Funny cats")
    with _app([v]) as c:
        r = c.get("/search", params={"q": "cats"})
    assert r.status_code == 200
    assert "Funny cats" in r.text
    assert "cats" in r.text  # query echoed into the search box


def test_search_empty_query_shows_empty_state() -> None:
    with _app([]) as c:
        r = c.get("/search", params={"q": ""})
    assert r.status_code == 200
    assert "empty" in r.text.lower() or "Ничего" in r.text


def test_home_empty_feed_shows_empty_state() -> None:
    with _app([]) as c:
        r = c.get("/")
    assert r.status_code == 200
    assert "empty" in r.text.lower() or "Пока нет" in r.text


def test_static_files_served() -> None:
    with _app([]) as c:
        assert c.get("/static/app.css").status_code == 200
        assert c.get("/static/app.js").status_code == 200
        assert c.get("/static/theme.js").status_code == 200


def test_templates_and_static_have_no_external_urls() -> None:
    files = list(TEMPLATES.glob("*.html")) + list(STATIC.glob("*.*"))
    assert files, "no template/static files found"
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"https?://", text), f"external URL in {f.name}"


def test_no_inline_scripts_handlers_or_safe() -> None:
    for f in TEMPLATES.glob("*.html"):
        text = f.read_text(encoding="utf-8")
        # every <script> must have src=
        for m in re.finditer(r"<script\b[^>]*>", text):
            assert "src=" in m.group(0), f"inline <script> in {f.name}"
        # no inline event handlers (on*=)
        assert not re.search(r"\son[a-z]+\s*=", text), f"inline handler in {f.name}"
        # no |safe for any data
        assert "|safe" not in text and "| safe" not in text, f"|safe in {f.name}"
    for f in STATIC.glob("*.js"):
        assert "eval(" not in f.read_text(encoding="utf-8")


def test_nginx_ships_csp_and_compose_media_origin() -> None:
    conf = (ROOT / "nginx" / "default.conf.template").read_text(encoding="utf-8")
    assert "Content-Security-Policy" in conf
    assert "script-src 'self'" in conf
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "CSP_MEDIA_ORIGIN" in compose


def test_xss_in_video_title_is_escaped() -> None:
    v = _video(title="<script>alert(1)</script>")
    with _app([v]) as c:
        r = c.get("/")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_unknown_page_is_404() -> None:
    with _app([]) as c:
        r = c.get(f"/watch/{uuid.uuid4()}")
    assert r.status_code == 404
