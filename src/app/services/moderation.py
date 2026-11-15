"""Moderation domain logic (FR-7, group 3E).

Reports: one per (video, reporter); only users who can actually see the
video may report it (404 otherwise — existence of private videos is hidden).
Resolutions: hide / unhide / dismiss, journaled on the report row
(who, when, why). A hidden video leaves feed/search/channel listings
(those queries filter status == 'ready') and play_url is issued only to
the owner and staff (see app.services.access.can_view).
"""

import uuid
from datetime import UTC, datetime
from typing import Protocol

from app.models import Video, VideoReport
from app.services import access

REPORT_REASONS = (
    "spam",
    "scam",
    "violence",
    "hate",
    "sexual",
    "misleading",
    "copyright",
    "other",
)

RESOLUTIONS = ("hide", "unhide", "dismiss")


class NotFound(Exception):
    """Video or report does not exist (or is deleted)."""


class DuplicateReport(Exception):
    """This user already reported this video."""


class AlreadyResolved(Exception):
    """The report was already decided."""


class InvalidTransition(Exception):
    """E.g. unhide on a video that is not hidden."""


class ReportRepo(Protocol):
    async def add(self, report: VideoReport) -> VideoReport: ...
    async def get(self, report_id: uuid.UUID) -> VideoReport | None: ...
    async def exists(self, video_id: uuid.UUID, reporter_id: uuid.UUID) -> bool: ...
    async def list(
        self, *, status: str | None, limit: int, offset: int
    ) -> tuple[list[VideoReport], int]: ...
    async def mark_resolved(
        self,
        report_id: uuid.UUID,
        *,
        resolved_by: uuid.UUID,
        resolved_at: datetime,
        resolution: str,
        note: str | None,
    ) -> VideoReport: ...


class VideoLookup(Protocol):
    async def get(self, video_id: uuid.UUID) -> Video | None: ...
    async def update(self, video: Video) -> Video: ...


async def submit_report(
    report_repo: ReportRepo,
    video_repo: VideoLookup,
    acl: access.AclRepo,
    video_id: uuid.UUID,
    user,
    reason: str,
    comment: str | None,
) -> VideoReport:
    video = await video_repo.get(video_id)
    if video is None or video.status == "deleted":
        raise NotFound("video not found")
    # Only someone who can see the video can report it; others get 404 so
    # the existence of private/hidden videos is not leaked.
    if not await access.can_view_with_acl(video, user, acl):
        raise NotFound("video not found")
    if await report_repo.exists(video_id, user.id):
        raise DuplicateReport("already reported")
    report = VideoReport(
        id=uuid.uuid4(),
        video_id=video_id,
        reporter_id=user.id,
        reason=reason,
        comment=comment,
        status="new",
        created_at=datetime.now(UTC),
    )
    return await report_repo.add(report)


async def resolve_report(
    report_repo: ReportRepo,
    video_repo: VideoLookup,
    report_id: uuid.UUID,
    *,
    action: str,
    note: str | None,
    moderator,
) -> tuple[VideoReport, Video]:
    report = await report_repo.get(report_id)
    if report is None:
        raise NotFound("report not found")
    if report.status != "new":
        raise AlreadyResolved("report already resolved")
    video = await video_repo.get(report.video_id)
    if video is None or video.status == "deleted":
        raise NotFound("video not found")
    if action == "hide":
        video.status = "hidden"
        await video_repo.update(video)
    elif action == "unhide":
        if video.status != "hidden":
            raise InvalidTransition("video is not hidden")
        video.status = "ready"
        await video_repo.update(video)
    # "dismiss": report only, video untouched
    resolved = await report_repo.mark_resolved(
        report_id,
        resolved_by=moderator.id,
        resolved_at=datetime.now(UTC),
        resolution=action,
        note=note,
    )
    return resolved, video
