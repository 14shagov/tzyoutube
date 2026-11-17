"""B: logout flow — JS path, redirect to home, header flips to «Войти».

The logout button handler in app.js must POST to the real route
POST /auth/logout (auth router has prefix=/auth, mounted without /api),
then redirect to "/". After the server clears the session cookie, a
following GET / renders the guest header with «Войти» and no logout-btn.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient
from test_viewing_api import FakeFeedRepo, FakeVideoRepo

from app.api import auth as auth_api
from app.api import videos as videos_api
from app.main import create_app
from app.services.fakes import FakeUserRepo
from app.services.rate_limit import InMemoryRateLimiter, get_rate_limiter

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "src" / "app" / "static" / "app.js"


def _client() -> TestClient:
    app = create_app()
    repo = FakeUserRepo()
    app.dependency_overrides[videos_api.get_feed_repo] = lambda: FakeFeedRepo([])
    app.dependency_overrides[videos_api.get_video_repo] = lambda: FakeVideoRepo([])
    app.dependency_overrides[auth_api.get_user_repo] = lambda: repo
    app.dependency_overrides[get_rate_limiter] = lambda: InMemoryRateLimiter()
    return TestClient(app)


def test_logout_handler_uses_real_auth_route_and_redirects_home() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    # handler is wired to #logout-btn
    assert 'getElementById("logout-btn")' in js
    # POST goes to /auth/logout — NOT the 404 /api/auth/logout
    assert '"/auth/logout"' in js
    assert "/api/auth/logout" not in js
    m = re.search(r'tzApi\("/auth/logout",\s*\{\s*method:\s*"POST"\s*\}\s*\)', js)
    assert m, "logout must POST /auth/logout via tzApi"
    # after success: redirect to home
    assert re.search(r'location\.href\s*=\s*"/"', js), "must redirect to / after logout"


def test_logout_flow_header_flips_to_login_button() -> None:
    with _client() as c:
        r = c.post(
            "/auth/register",
            json={"email": "b@example.com", "password": "password123", "channel_name": "B"},
        )
        assert r.status_code in (200, 201), r.text
        r = c.post("/auth/login", json={"email": "b@example.com", "password": "password123"})
        assert r.status_code == 200, r.text

        home = c.get("/")
        assert home.status_code == 200
        assert 'id="logout-btn"' in home.text, "logged-in header must show Выйти"
        assert "Выйти" in home.text

        r = c.post("/auth/logout")
        assert r.status_code == 204, r.text

        home = c.get("/")
        assert home.status_code == 200
        assert "Войти" in home.text, "after logout header must show Войти"
        assert 'id="logout-btn"' not in home.text
