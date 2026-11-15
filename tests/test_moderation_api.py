"""3E red tests: moderation reports + admin role/block (FR-7, FR-8).

POST /api/videos/{id}/report           — auth, one per user+video, reason from list
GET  /api/mod/reports?status=&limit=&offset — moderator/admin only
POST /api/mod/reports/{id}/resolve     — hide | unhide | dismiss, audit journal
PATCH /api/admin/users/{id}            — admin only: role, is_blocked
Hidden video: no feed/search/channel entry, play_url only for owner+staff.

The API module app.api.moderation lands in the fix commit, so this file
fails at collection (red) until then.
"""

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.api import moderation as moderation_api  # noqa: F401  (lands in fix commit)
from app.api import videos as videos_api
from app.main import create_app
from app.models import VideoReport
from app.services.fakes import FakeUserRepo
from app.services.rate_limit import InMemoryRateLimiter, get_rate_limiter
from test_channels_api import _user
from test_video_manage_api import FakeAclRepo, FakeVideoRepo, _video


class FakeStorage:
    def presigned_get(self, bucket, key, ttl_seconds=None, response_headers=None, *, private=False):
        return f"https://s.example/{bucket}/{key}?sig=1"


class FakeReportRepo:
    def __init__(self, reports=()):
        self.reports = {r.id: r for r in reports}
        self.by_key = {(r.video_id, r.reporter_id): r.id for r in reports}

    async def add(self, report):
        self.reports[report.id] = report
        self.by_key[(report.video_id, report.reporter_id)] = report.id
        return report

    async def get(self, report_id):
        return self.reports.get(report_id)

    async def exists(self, video_id, reporter_id):
        return (video_id, reporter_id) in self.by_key

    async def list(self, *, status=None, limit, offset):
        items = [r for r in self.reports.values() if status is None or r.status == status]
        items.sort(key=lambda r: r.created_at, reverse=True)
        return items[offset : offset + limit], len(items)

    async def mark_resolved(self, report_id, *, resolved_by, resolved_at, resolution, note):
        r = self.reports[report_id]
        r.status = "dismissed" if resolution == "dismiss" else "resolved"
        r.resolution = resolution
        r.resolution_note = note
        r.resolved_by = resolved_by
        r.resolved_at = resolved_at
        return r


def _report(video_id, reporter_id, reason="spam", comment=None, status="new"):
    return VideoReport(
        id=uuid.uuid4(),
        video_id=video_id,
        reporter_id=reporter_id,
        reason=reason,
        comment=comment,
        status=status,
        created_at=datetime.now(UTC),
    )


def _client(report_repo, video_repo, acl=None, user_repo=None, current=None, optional=None):
    app = create_app()
    app.dependency_overrides[moderation_api.get_report_repo] = lambda: report_repo
    app.dependency_overrides[videos_api.get_video_repo] = lambda: video_repo
    app.dependency_overrides[videos_api.get_acl] = lambda: acl or FakeAclRepo()
    app.dependency_overrides[videos_api.get_storage] = lambda: FakeStorage()
    if user_repo is not None:
        app.dependency_overrides[auth_api.get_user_repo] = lambda: user_repo
        app.dependency_overrides[get_rate_limiter] = lambda: InMemoryRateLimiter()
    if current is not None:
        app.dependency_overrides[auth_api.get_current_user] = lambda: current
    # guest-tolerant user for card endpoints: mutable so a test can switch it
    holder = {"user": optional}
    app.dependency_overrides[videos_api._optional_user] = lambda: holder["user"]
    return TestClient(app), holder


# --- POST /api/videos/{id}/report ------------------------------------------


def test_report_video_201() -> None:
    owner = _user()
    reporter = _user(email="r@example.com", channel_name="rep")
    video = _video(owner.id)
    client, _ = _client(FakeReportRepo(), FakeVideoRepo([video]), current=reporter)
    r = client.post(f"/api/videos/{video.id}/report", json={"reason": "spam", "comment": "ads"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["video_id"] == str(video.id)
    assert body["reporter_id"] == str(reporter.id)
    assert body["reason"] == "spam"
    assert body["comment"] == "ads"
    assert body["status"] == "new"


def test_report_duplicate_409() -> None:
    owner = _user()
    reporter = _user(email="r@example.com", channel_name="rep")
    video = _video(owner.id)
    repo = FakeReportRepo([_report(video.id, reporter.id)])
    client, _ = _client(repo, FakeVideoRepo([video]), current=reporter)
    r = client.post(f"/api/videos/{video.id}/report", json={"reason": "scam"})
    assert r.status_code == 409


def test_report_guest_401() -> None:
    owner = _user()
    video = _video(owner.id)
    client, _ = _client(FakeReportRepo(), FakeVideoRepo([video]), current=None)
    r = client.post(f"/api/videos/{video.id}/report", json={"reason": "spam"})
    assert r.status_code == 401


def test_report_unknown_video_404() -> None:
    reporter = _user(email="r@example.com", channel_name="rep")
    client, _ = _client(FakeReportRepo(), FakeVideoRepo(), current=reporter)
    r = client.post(f"/api/videos/{uuid.uuid4()}/report", json={"reason": "spam"})
    assert r.status_code == 404


def test_report_foreign_private_video_404() -> None:
    owner = _user()
    reporter = _user(email="r@example.com", channel_name="rep")
    video = _video(owner.id, is_private=True)
    client, _ = _client(FakeReportRepo(), FakeVideoRepo([video]), current=reporter)
    r = client.post(f"/api/videos/{video.id}/report", json={"reason": "spam"})
    assert r.status_code == 404  # cannot see it -> cannot report it


def test_report_invalid_reason_422() -> None:
    owner = _user()
    reporter = _user(email="r@example.com", channel_name="rep")
    video = _video(owner.id)
    client, _ = _client(FakeReportRepo(), FakeVideoRepo([video]), current=reporter)
    r = client.post(f"/api/videos/{video.id}/report", json={"reason": "bogus"})
    assert r.status_code == 422


# --- GET /api/mod/reports ----------------------------------------------------


def test_mod_reports_role_matrix_and_filter() -> None:
    owner = _user()
    video = _video(owner.id)
    mod = _user(email="m@example.com", channel_name="mod", role="moderator")
    admin = _user(email="a@example.com", channel_name="adm", role="admin")
    plain = _user(email="p@example.com", channel_name="pln")
    reports = FakeReportRepo([
        _report(video.id, owner.id, reason="spam"),
        _report(video.id, plain.id, reason="scam"),
    ])
    video_repo = FakeVideoRepo([video])

    client, _ = _client(reports, video_repo, current=None)
    assert client.get("/api/mod/reports").status_code == 401
    client, _ = _client(reports, video_repo, current=plain)
    assert client.get("/api/mod/reports").status_code == 403
    for staff in (mod, admin):
        client, _ = _client(reports, video_repo, current=staff)
        r = client.get("/api/mod/reports")
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 2
        r = client.get("/api/mod/reports", params={"status": "new", "limit": 1})
        assert r.status_code == 200
        assert r.json()["total"] == 2 and len(r.json()["items"]) == 1
        assert client.get("/api/mod/reports", params={"limit": 101}).status_code == 422


# --- POST /api/mod/reports/{id}/resolve --------------------------------------


def test_resolve_hide_sets_video_hidden_and_audit() -> None:
    owner = _user()
    mod = _user(email="m@example.com", channel_name="mod", role="moderator")
    video = _video(owner.id)
    report = _report(video.id, owner.id)
    reports = FakeReportRepo([report])
    video_repo = FakeVideoRepo([video])
    client, _ = _client(reports, video_repo, current=mod)
    r = client.post(
        f"/api/mod/reports/{report.id}/resolve",
        json={"action": "hide", "note": "spam confirmed"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["video_status"] == "hidden"
    rep = body["report"]
    assert rep["status"] == "resolved"
    assert rep["resolution"] == "hide"
    assert rep["resolution_note"] == "spam confirmed"
    assert rep["resolved_by"] == str(mod.id)
    assert rep["resolved_at"] is not None
    assert video_repo.videos[video.id].status == "hidden"


def test_resolve_twice_409() -> None:
    owner = _user()
    mod = _user(email="m@example.com", channel_name="mod", role="moderator")
    video = _video(owner.id)
    report = _report(video.id, owner.id, status="resolved")
    client, _ = _client(FakeReportRepo([report]), FakeVideoRepo([video]), current=mod)
    r = client.post(f"/api/mod/reports/{report.id}/resolve", json={"action": "hide"})
    assert r.status_code == 409


def test_resolve_unhide_requires_hidden_then_ok() -> None:
    owner = _user()
    mod = _user(email="m@example.com", channel_name="mod", role="moderator")
    video = _video(owner.id, status="ready")
    report = _report(video.id, owner.id)
    reports = FakeReportRepo([report])
    video_repo = FakeVideoRepo([video])
    client, _ = _client(reports, video_repo, current=mod)
    # cannot unhide a video that is not hidden
    r = client.post(f"/api/mod/reports/{report.id}/resolve", json={"action": "unhide"})
    assert r.status_code == 422
    video.status = "hidden"
    r = client.post(f"/api/mod/reports/{report.id}/resolve", json={"action": "unhide"})
    assert r.status_code == 200, r.text
    assert r.json()["video_status"] == "ready"
    assert video_repo.videos[video.id].status == "ready"


def test_resolve_dismiss_keeps_video_status() -> None:
    owner = _user()
    mod = _user(email="m@example.com", channel_name="mod", role="moderator")
    video = _video(owner.id, status="ready")
    report = _report(video.id, owner.id)
    reports = FakeReportRepo([report])
    video_repo = FakeVideoRepo([video])
    client, _ = _client(reports, video_repo, current=mod)
    r = client.post(f"/api/mod/reports/{report.id}/resolve", json={"action": "dismiss"})
    assert r.status_code == 200, r.text
    assert r.json()["report"]["status"] == "dismissed"
    assert r.json()["video_status"] == "ready"


def test_resolve_role_matrix_and_unknown() -> None:
    owner = _user()
    mod = _user(email="m@example.com", channel_name="mod", role="moderator")
    plain = _user(email="p@example.com", channel_name="pln")
    video = _video(owner.id)
    report = _report(video.id, owner.id)
    reports = FakeReportRepo([report])
    video_repo = FakeVideoRepo([video])
    client, _ = _client(reports, video_repo, current=None)
    assert client.post(f"/api/mod/reports/{report.id}/resolve", json={"action": "hide"}).status_code == 401
    client, _ = _client(reports, video_repo, current=plain)
    assert client.post(f"/api/mod/reports/{report.id}/resolve", json={"action": "hide"}).status_code == 403
    client, _ = _client(reports, video_repo, current=mod)
    assert client.post(
        f"/api/mod/reports/{uuid.uuid4()}/resolve", json={"action": "hide"}
    ).status_code == 404


# --- hidden video delivery ----------------------------------------------------


def test_hidden_card_owner_gets_play_url_others_404() -> None:
    owner = _user()
    video = _video(owner.id, status="hidden")
    video.video_key = "videos/x.mp4"
    stranger = _user(email="s@example.com", channel_name="str")
    video_repo = FakeVideoRepo([video])
    client, holder = _client(FakeReportRepo(), video_repo, current=None, optional=owner)
    r = client.get(f"/api/videos/{video.id}")
    assert r.status_code == 200, r.text
    assert r.json()["play_url"] is not None  # owner may still play it
    holder["user"] = stranger
    assert client.get(f"/api/videos/{video.id}").status_code == 404
    holder["user"] = None
    assert client.get(f"/api/videos/{video.id}").status_code == 404


# --- PATCH /api/admin/users/{id} ----------------------------------------------


def test_admin_role_and_block_matrix() -> None:
    user_repo = FakeUserRepo()
    app = create_app()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: user_repo
    app.dependency_overrides[get_rate_limiter] = lambda: InMemoryRateLimiter()
    app.dependency_overrides[moderation_api.get_report_repo] = lambda: FakeReportRepo()
    admin = _user(email="a@example.com", channel_name="adm", role="admin")
    app.dependency_overrides[auth_api.get_current_user] = lambda: admin

    # register a plain user through the real auth endpoints
    plain_client = TestClient(app)
    r = plain_client.post("/auth/register", json={
        "email": "u@example.com", "password": "password123", "channel_name": "UserOne",
    })
    assert r.status_code == 201, r.text
    assert plain_client.post("/auth/login", json={
        "email": "u@example.com", "password": "password123",
    }).status_code == 200
    target = r.json()["id"]

    admin_client = TestClient(app)
    # role change: user -> moderator, effective immediately (role re-read per request)
    r = admin_client.patch(f"/api/admin/users/{target}", json={"role": "moderator"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "moderator"
    assert plain_client.get("/api/mod/reports").status_code == 200
    # block: existing session dies, new login refused
    r = admin_client.patch(f"/api/admin/users/{target}", json={"is_blocked": True})
    assert r.status_code == 200 and r.json()["is_blocked"] is True
    assert plain_client.get("/auth/me").status_code == 401
    assert plain_client.post("/auth/login", json={
        "email": "u@example.com", "password": "password123",
    }).status_code == 401
    # guards
    assert admin_client.patch(f"/api/admin/users/{admin.id}", json={"role": "user"}).status_code == 422
    assert admin_client.patch(f"/api/admin/users/{uuid.uuid4()}", json={"role": "admin"}).status_code == 404


def test_admin_endpoint_forbidden_for_non_admin() -> None:
    mod = _user(email="m@example.com", channel_name="mod", role="moderator")
    plain = _user(email="p@example.com", channel_name="pln")
    app = create_app()
    app.dependency_overrides[auth_api.get_user_repo] = lambda: FakeUserRepo()
    app.dependency_overrides[moderation_api.get_report_repo] = lambda: FakeReportRepo()
    for user, code in ((None, 401), (plain, 403), (mod, 403)):
        if user is not None:
            app.dependency_overrides[auth_api.get_current_user] = lambda u=user: u
        else:
            app.dependency_overrides.pop(auth_api.get_current_user, None)
        c = TestClient(app)
        r = c.patch(f"/api/admin/users/{uuid.uuid4()}", json={"role": "admin"})
        assert r.status_code == code, (user, r.status_code, r.text)
