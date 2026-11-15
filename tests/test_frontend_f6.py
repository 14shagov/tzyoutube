"""F6 red tests — moderation & admin UI (/mod page) + GET /api/admin/users list.

Covers: role matrix on the page (guest → 303 login, user → 403 page,
moderator → 200, admin → 200 + user admin table), reports table renders with
reason/comment/video link and resolve controls, XSS escaping of report text,
admin user list API (list_all on UserRepo), and the new route in openapi.
"""

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from test_moderation_api import FakeReportRepo
from test_video_manage_api import _video

from app.api import auth as auth_api
from app.api import moderation as moderation_api
from app.main import create_app
from app.models import VideoReport
from app.services import access
from app.services.fakes import FakeUserRepo


def _user(role="user", email="u@example.com", channel_name="user1"):
    from test_channels_api import _user as _mk

    u = _mk(role=role, email=email, channel_name=channel_name)
    u.is_blocked = False  # UserPublic requires a real bool
    return u


def _report(video_id, reporter_id, reason="spam", comment="bad <script>alert(1)</script>"):
    return VideoReport(
        id=uuid.uuid4(),
        video_id=video_id,
        reporter_id=reporter_id,
        reason=reason,
        comment=comment,
        status="new",
        created_at=datetime.now(UTC),
    )


def _app(report_repo, user_repo=None, current=None):
    app = create_app()
    user_repo = user_repo or FakeUserRepo()
    app.dependency_overrides[moderation_api.get_report_repo] = lambda: report_repo
    app.dependency_overrides[auth_api.get_user_repo] = lambda: user_repo
    if current is not None:
        app.dependency_overrides[auth_api.get_current_user] = lambda: current
        from app.api import videos as videos_api

        app.dependency_overrides[videos_api._optional_user] = lambda: current
    else:
        from app.api import videos as videos_api

        app.dependency_overrides[videos_api._optional_user] = lambda: None
    return TestClient(app), user_repo


# --- API: GET /api/admin/users -------------------------------------------------


def test_admin_users_list_requires_admin():
    repo = FakeUserRepo()
    admin = _user(role="admin")
    mod = _user(role="moderator", email="m@example.com", channel_name="mod")
    plain = _user(role="user", email="p@example.com", channel_name="plain")
    repo.users.update({u.id: u for u in (admin, mod, plain)})

    # guest -> 401 (real get_current_user, no cookie)
    c, _ = _app(FakeReportRepo(), user_repo=repo)
    assert c.get("/api/admin/users").status_code == 401

    for role_user, expected in ((plain, 403), (mod, 403), (admin, 200)):
        c, _ = _app(FakeReportRepo(), user_repo=repo, current=role_user)
        r = c.get("/api/admin/users")
        assert r.status_code == expected, (role_user.role, r.status_code)
        if expected == 200:
            body = r.json()
            assert body["total"] == 3
            assert {u["email"] for u in body["items"]} == {
                "u@example.com", "m@example.com", "p@example.com"}


def test_admin_users_route_in_openapi():
    spec = create_app().openapi()
    assert "/api/admin/users" in spec["paths"]
    assert "get" in spec["paths"]["/api/admin/users"]
    schema = spec["paths"]["/api/admin/users"]["get"]["responses"]["200"]
    ref = schema["content"]["application/json"]["schema"]
    assert "$ref" in ref, "response must be a typed Pydantic model"


# --- Page: GET /mod -------------------------------------------------------------


def test_mod_page_guest_redirects_to_login():
    c, _ = _app(FakeReportRepo())
    r = c.get("/mod", follow_redirects=False)
    assert r.status_code == 303
    assert "/login?next=/mod" in r.headers["location"]


def test_mod_page_plain_user_gets_403_page():
    c, _ = _app(FakeReportRepo(), current=_user(role="user"))
    r = c.get("/mod")
    assert r.status_code == 403
    assert 'data-testid="error-page"' in r.text
    assert "403" in r.text


def test_mod_page_moderator_sees_reports():
    reporter = _user(email="r@example.com", channel_name="reporter")
    mod = _user(role="moderator", email="m@example.com", channel_name="mod")
    user_repo = FakeUserRepo()
    user_repo.users[reporter.id] = reporter
    user_repo.users[mod.id] = mod
    v = _video(reporter.id, title="Bad video")
    repo = FakeReportRepo()
    rep = _report(v.id, reporter.id)
    repo.reports[rep.id] = rep
    c, _ = _app(repo, user_repo=user_repo, current=mod)
    r = c.get("/mod")
    assert r.status_code == 200
    body = r.text
    assert 'data-testid="reports-table"' in body
    assert "spam" in body
    assert "bad &lt;script&gt;" in body  # XSS-escaped comment
    assert "<script>alert(1)</script>" not in body
    assert f"/watch/{v.id}" in body
    assert 'data-action="hide"' in body
    assert 'data-action="unhide"' in body
    assert 'data-action="dismiss"' in body
    assert 'name="note"' in body
    assert "/static/mod.js" in body
    # no inline handlers
    import re

    assert not re.search(r"\son\w+\s*=", body), "no inline event handlers"
    # admin tab hidden for moderator
    assert 'data-testid="admin-users"' not in body


def test_mod_page_admin_sees_users_table():
    admin = _user(role="admin", email="a@example.com", channel_name="admin")
    plain = _user(email="p@example.com", channel_name="plain")
    user_repo = FakeUserRepo()
    user_repo.users[admin.id] = admin
    user_repo.users[plain.id] = plain
    c, _ = _app(FakeReportRepo(), user_repo=user_repo, current=admin)
    r = c.get("/mod")
    assert r.status_code == 200
    body = r.text
    assert 'data-testid="admin-users"' in body
    assert "p@example.com" in body
    assert 'name="role"' in body
    assert 'name="is_blocked"' in body
    assert str(plain.id) in body


def test_mod_page_staff_role_constant_used():
    # sanity: STAFF_ROLES still the single source of truth for the sidebar + page
    assert access.STAFF_ROLES == {"moderator", "admin"}
