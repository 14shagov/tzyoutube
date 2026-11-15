"""F2 (owner m02359): login/register pages + header user state.

Red before the routes exist. Pages must render for guests, redirect an
already-authenticated user away, carry ?next= through, keep the header in
sync (channel name + Выйти vs Войти), and escape user data (XSS).
"""

import uuid

from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.api import videos as videos_api
from app.main import create_app
from app.services.fakes import FakeUserRepo
from test_viewing_api import FakeFeedRepo, FakeUser


def _app(user=None) -> TestClient:
    app = create_app()
    app.dependency_overrides[videos_api.get_feed_repo] = lambda: FakeFeedRepo([])
    app.dependency_overrides[auth_api.get_user_repo] = lambda: FakeUserRepo()
    if user is not None:
        app.dependency_overrides[videos_api._optional_user] = lambda: user
        app.dependency_overrides[auth_api.get_current_user] = lambda: user
    return TestClient(app)


def test_login_page_guest_200() -> None:
    with _app() as c:
        r = c.get("/login")
    assert r.status_code == 200
    body = r.text
    assert 'id="login-form"' in body
    assert 'name="email"' in body and 'name="password"' in body
    assert "/register" in body  # switch link
    assert "Войти" in body or "Вход" in body


def test_register_page_guest_200() -> None:
    with _app() as c:
        r = c.get("/register")
    assert r.status_code == 200
    body = r.text
    assert 'id="register-form"' in body
    assert 'name="channel_name"' in body
    assert "/login" in body


def test_login_page_keeps_next_param() -> None:
    with _app() as c:
        r = c.get("/login", params={"next": "/upload"})
    assert r.status_code == 200
    assert 'name="next"' in r.text and "/upload" in r.text


def test_auth_forms_have_no_inline_handlers() -> None:
    with _app() as c:
        for path in ("/login", "/register"):
            body = c.get(path).text
            assert "onsubmit" not in body
            # JS (external auth.js) owns the submit; the form itself has no action
            assert 'action="/api' not in body


def test_login_page_redirects_when_authenticated() -> None:
    with _app(user=FakeUser()) as c:
        r = c.get("/login", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_header_shows_user_when_logged_in() -> None:
    user = FakeUser()
    user.channel_name = "CoolChannel"
    with _app(user=user) as c:
        r = c.get("/")
    assert r.status_code == 200
    assert "CoolChannel" in r.text
    assert "Выйти" in r.text
    assert ">Войти<" not in r.text
    assert f'/channel/{user.id}' in r.text  # avatar links to own channel


def test_header_shows_login_for_guest() -> None:
    with _app() as c:
        r = c.get("/")
    assert ">Войти<" in r.text
    assert "Выйти" not in r.text


def test_header_xss_channel_name_escaped() -> None:
    user = FakeUser()
    user.channel_name = "<b>evil</b>"
    with _app(user=user) as c:
        r = c.get("/")
    assert "<b>evil</b>" not in r.text
    assert "&lt;b&gt;evil&lt;/b&gt;" in r.text


def test_mod_link_only_for_staff() -> None:
    plain = FakeUser(role="user")
    mod = FakeUser(role="moderator")
    with _app(user=plain) as c:
        assert "/mod" not in c.get("/").text
    with _app(user=mod) as c:
        assert 'href="/mod"' in c.get("/").text


def test_auth_js_file_served() -> None:
    with _app() as c:
        r = c.get("/static/auth.js")
    assert r.status_code == 200
    assert "tzApi" in r.text
