"""HTTP layer for moderation and admin (FR-7, FR-8, group 3E).

POST /api/videos/{video_id}/report        — any user who can see the video
GET  /api/mod/reports                     — moderator/admin, ?status= filter
POST /api/mod/reports/{id}/resolve        — hide | unhide | dismiss (journaled)
PATCH /api/admin/users/{user_id}          — admin only: role, is_blocked

All responses are Pydantic models (no bare dicts). Repos are injected via
dependencies so tests override them with fakes; the providers are the SAME
function objects as in videos_api/auth_api so existing overrides apply.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user, get_user_repo
from app.api.deps import require_role
from app.api.videos import get_acl, get_video_repo
from app.core.db import get_db_session
from app.models import User
from app.schemas import UserPublic
from app.services import moderation as moderation_svc

router = APIRouter(prefix="/api", tags=["moderation"])


class ReportIn(BaseModel):
    reason: Literal[
        "spam", "scam", "violence", "hate", "sexual", "misleading", "copyright", "other"
    ]
    comment: str | None = Field(default=None, max_length=1000)


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    video_id: uuid.UUID
    reporter_id: uuid.UUID
    reason: str
    comment: str | None = None
    status: str
    resolution: str | None = None
    resolution_note: str | None = None
    resolved_by: uuid.UUID | None = None
    resolved_at: datetime | None = None
    created_at: datetime


class ReportListOut(BaseModel):
    items: list[ReportOut]
    total: int


class ResolveIn(BaseModel):
    action: Literal["hide", "unhide", "dismiss"]
    note: str | None = Field(default=None, max_length=500)


class ResolveOut(BaseModel):
    report: ReportOut
    video_status: str


class AdminUserPatch(BaseModel):
    role: Literal["user", "moderator", "admin"] | None = None
    is_blocked: bool | None = None


def get_report_repo(
    session: AsyncSession = Depends(get_db_session),
):
    from app.api.repos import SqlReportRepo

    return SqlReportRepo(session)


@router.post(
    "/videos/{video_id}/report",
    status_code=http_status.HTTP_201_CREATED,
    response_model=ReportOut,
)
async def report_video(
    video_id: uuid.UUID,
    payload: ReportIn,
    user: User = Depends(get_current_user),
    report_repo=Depends(get_report_repo),
    video_repo=Depends(get_video_repo),
    acl=Depends(get_acl),
) -> ReportOut:
    try:
        report = await moderation_svc.submit_report(
            report_repo,
            video_repo,
            acl,
            video_id,
            user,
            reason=payload.reason,
            comment=payload.comment,
        )
    except moderation_svc.NotFound:
        raise HTTPException(404, "not found") from None
    except moderation_svc.DuplicateReport:
        raise HTTPException(409, "already reported") from None
    return ReportOut.model_validate(report)


@router.get("/mod/reports", response_model=ReportListOut)
async def list_reports(
    status: Literal["new", "resolved", "dismissed"] | None = Query(default=None),
    limit: Annotated[int, Query(ge=0, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    user: User = Depends(require_role("moderator", "admin")),
    report_repo=Depends(get_report_repo),
) -> ReportListOut:
    items, total = await report_repo.list(status=status, limit=limit, offset=offset)
    return ReportListOut(items=[ReportOut.model_validate(r) for r in items], total=total)


@router.post("/mod/reports/{report_id}/resolve", response_model=ResolveOut)
async def resolve_report(
    report_id: uuid.UUID,
    payload: ResolveIn,
    user: User = Depends(require_role("moderator", "admin")),
    report_repo=Depends(get_report_repo),
    video_repo=Depends(get_video_repo),
) -> ResolveOut:
    try:
        report, video = await moderation_svc.resolve_report(
            report_repo,
            video_repo,
            report_id,
            action=payload.action,
            note=payload.note,
            moderator=user,
        )
    except moderation_svc.NotFound:
        raise HTTPException(404, "not found") from None
    except moderation_svc.AlreadyResolved:
        raise HTTPException(409, "report already resolved") from None
    except moderation_svc.InvalidTransition as exc:
        raise HTTPException(422, str(exc)) from None
    return ResolveOut(
        report=ReportOut.model_validate(report), video_status=video.status
    )


@router.patch("/admin/users/{user_id}", response_model=UserPublic)
async def admin_patch_user(
    user_id: uuid.UUID,
    payload: AdminUserPatch,
    admin: User = Depends(require_role("admin")),
    user_repo=Depends(get_user_repo),
) -> UserPublic:
    """FR-8: admin changes role / blocks a user. Role is re-read from the DB
    on every request (resolve_session joins User), so no re-login is needed;
    a blocked user's sessions are rejected and new logins refused."""
    if user_id == admin.id:
        raise HTTPException(422, "cannot change your own account")
    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise HTTPException(404, "user not found")
    if payload.role is not None:
        target.role = payload.role
    if payload.is_blocked is not None:
        target.is_blocked = payload.is_blocked
    await user_repo.update_user(target)
    return UserPublic.model_validate(target)
