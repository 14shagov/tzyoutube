"""SSR pages (stage 3 UI, ADR-007): Jinja2 templates + small external JS.

All HTML routes live in this module; the JSON API stays in app/api/*. Page
handlers reuse the SAME dependency providers as the API modules, so tests
override them once and both API and pages see the fakes.

Rules (owner spec m02359): no external CDN/fonts, no inline <script> or
on*= handlers (CSP script-src 'self'), all JS in /static/*.js, Jinja
autoescape on and NO |safe for user data anywhere.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api import auth as auth_api
from app.api import channels as channels_api
from app.api import playlists as playlists_api
from app.api import videos as videos_api
from app.core.storage import BUCKETS

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["pages"])

BRAND = "TZ Video"


# --- Jinja filters -----------------------------------------------------------


def fmt_duration(seconds) -> str:
    """10 -> '0:10', 754 -> '12:34', 3725 -> '1:02:05'."""
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return "0:00"
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def time_ago(dt) -> str:
    """Russian relative date for cards ('сегодня', '3 дн. назад', '05.11.2026')."""
    if dt is None:
        return ""
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=UTC)
    days = (datetime.now(UTC) - dt).days
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    if days < 7:
        return f"{days} дн. назад"
    if days < 30:
        return f"{days // 7} нед. назад"
    if days < 365:
        return f"{days // 30} мес. назад"
    return dt.strftime("%d.%m.%Y")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian plural form selector."""
    n = abs(int(n)) % 100
    if 10 < n < 20:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


templates.env.filters["fmt_duration"] = fmt_duration
templates.env.filters["time_ago"] = time_ago
templates.env.filters["plural"] = plural


# --- shared context helpers ---------------------------------------------------


def _public_user(user) -> dict | None:
    """Template-safe view of the current user (None for guests)."""
    if user is None:
        return None
    return {
        "id": str(user.id),
        "channel_name": user.channel_name,
        "role": getattr(user, "role", "user"),
    }


def _thumb(video) -> str | None:
    key = getattr(video, "thumbnail_key", None)
    if not key or getattr(video, "is_private", False):
        return None
    return f"/media/{key}"


async def _feed_cards(videos, user_repo) -> list[dict]:
    """Assemble grid cards; owner channel names via user_repo.get_by_id."""
    cards = []
    for v in videos:
        owner = await user_repo.get_by_id(v.user_id)
        views = int(getattr(v, "view_count", 0) or 0)
        cards.append({
            "id": v.id,
            "title": v.title,
            "thumb": _thumb(v),
            "duration": fmt_duration(getattr(v, "duration_seconds", 0)),
            "views_label": f"{views} {plural(views, 'просмотр', 'просмотра', 'просмотров')}",
            "channel_name": owner.channel_name if owner is not None else "—",
            "channel_id": v.user_id,
            "date": time_ago(getattr(v, "created_at", None)),
        })
    return cards


# --- F2: login / register ------------------------------------------------------


def _safe_next(next_url: str) -> str:
    """Only same-site absolute paths are allowed as redirect targets."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(
    request: Request,
    next: str = "/",
    user=Depends(videos_api._optional_user),
):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "auth.html", {
        "request": request, "user": None, "page": "auth", "brand": BRAND,
        "mode": "login", "next": _safe_next(next),
    })


@router.get("/register", response_class=HTMLResponse, include_in_schema=False)
async def register_page(
    request: Request,
    next: str = "/",
    user=Depends(videos_api._optional_user),
):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "auth.html", {
        "request": request, "user": None, "page": "auth", "brand": BRAND,
        "mode": "register", "next": _safe_next(next),
    })


# --- F1: home + search ---------------------------------------------------------


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(
    request: Request,
    feed_repo=Depends(videos_api.get_feed_repo),
    user_repo=Depends(auth_api.get_user_repo),
    user=Depends(videos_api._optional_user),
):
    videos = await feed_repo.public_feed(limit=24, offset=0)
    cards = await _feed_cards(videos, user_repo)
    return templates.TemplateResponse(request, "home.html", {
        "request": request,
        "user": _public_user(user),
        "page": "home",
        "brand": BRAND,
        "q": "",
        "cards": cards,
        "chips": ["Все", "Музыка", "Игры", "Новости", "Спорт", "Обучение", "Наука"],
        "empty_title": "Пока нет видео",
        "empty_sub": "Загрузите первое видео — кнопка «Загрузить» в шапке.",
    })


@router.get("/search", response_class=HTMLResponse, include_in_schema=False)
async def search_page(
    request: Request,
    q: str = "",
    feed_repo=Depends(videos_api.get_feed_repo),
    user_repo=Depends(auth_api.get_user_repo),
    user=Depends(videos_api._optional_user),
):
    query = q.strip()
    videos = await feed_repo.search(query, limit=24, offset=0) if query else []
    cards = await _feed_cards(videos, user_repo)
    return templates.TemplateResponse(request, "home.html", {
        "request": request,
        "user": _public_user(user),
        "page": "search",
        "brand": BRAND,
        "q": query,
        "cards": cards,
        "chips": [],
        "empty_title": "Ничего не найдено",
        "empty_sub": "Попробуйте изменить запрос.",
    })


# --- F3: watch page ------------------------------------------------------------


@router.get("/watch/{video_id}", response_class=HTMLResponse, include_in_schema=False)
async def watch_page(
    request: Request,
    video_id: uuid.UUID,
    user=Depends(videos_api._optional_user),
    video_repo=Depends(videos_api.get_video_repo),
    feed_repo=Depends(videos_api.get_feed_repo),
    reaction_repo=Depends(videos_api.get_reaction_repo),
    comment_repo=Depends(videos_api.get_comment_repo),
    acl=Depends(videos_api.get_acl),
    storage=Depends(videos_api.get_storage),
    user_repo=Depends(auth_api.get_user_repo),
    channel_repo=Depends(channels_api.get_channel_repo),
    sub_repo=Depends(channels_api.get_subscription_repo),
):
    """F3: two-column watch page. Existence is hidden (404) for anyone who
    cannot view — same policy as the JSON card."""
    from app.services import access

    video = await video_repo.get(video_id)
    if video is None or video.status == "deleted" or not await access.can_view_with_acl(
        video, user, acl
    ):
        raise HTTPException(404, "not found")

    play_url = await access.view_or_none(storage, video, user, acl)
    owner = await user_repo.get_by_id(video.user_id)
    likes, dislikes = await reaction_repo.counts(video_id)
    my_reaction = await reaction_repo.get(user.id, video_id) if user is not None else None
    comments = await comment_repo.list_for_video(video_id, limit=100, offset=0)
    author_ids = {c.user_id for c in comments if getattr(c, "user_id", None) is not None}
    authors = {u.id: u for u in await user_repo.get_by_ids(author_ids)} if author_ids else {}
    comment_rows = [
        {
            "id": c.id,
            "text": c.text,
            "parent_id": getattr(c, "parent_id", None),
            "author": authors[getattr(c, "user_id", None)].channel_name
            if getattr(c, "user_id", None) in authors else "—",
            "date": time_ago(getattr(c, "created_at", None)),
        }
        for c in comments
    ]
    top_comments = [c for c in comment_rows if c["parent_id"] is None]
    replies = [c for c in comment_rows if c["parent_id"] is not None]
    subscribed = await sub_repo.is_subscribed(user.id, video.user_id) if user is not None else False
    sub_count = await channel_repo.subscriber_count(video.user_id)
    views = int(getattr(video, "view_count", 0) or 0)

    rec_videos = await feed_repo.public_feed(limit=24, offset=0)
    recs = await _feed_cards([v for v in rec_videos if v.id != video_id][:12], user_repo)

    # Thumbnail: public videos via nginx /media/, private via presigned URL
    # (response-content-type forces image/jpeg — covers legacy objects).
    thumb_key = getattr(video, "thumbnail_key", None)
    if thumb_key and not getattr(video, "is_private", False):
        thumb_url = f"/media/{thumb_key}"
    elif thumb_key:
        thumb_url = storage.presigned_get(  # sync: minio signs locally, no network
            BUCKETS["thumbnails"], thumb_key,
            response_headers={"response-content-type": "image/jpeg"},
            private=True,
        )
    else:
        thumb_url = None

    return templates.TemplateResponse(request, "watch.html", {
        "request": request,
        "user": _public_user(user),
        "page": "watch",
        "brand": BRAND,
        "video": video,
        "play_url": play_url,
        "thumb_url": thumb_url,
        "owner_name": owner.channel_name if owner is not None else "—",
        "owner_id": video.user_id,
        "is_owner": user is not None and user.id == video.user_id,
        "likes": likes,
        "dislikes": dislikes,
        "my_reaction": my_reaction,
        "views_label": f"{views} {plural(views, 'просмотр', 'просмотра', 'просмотров')}",
        "date": time_ago(getattr(video, "created_at", None)),
        "subscribed": subscribed,
        "sub_count": sub_count,
        "sub_label": f"{sub_count} {plural(sub_count, 'подписчик', 'подписчика', 'подписчиков')}",
        "comments": top_comments,
        "replies": {r["parent_id"]: r for r in replies},
        "comment_count": len(comment_rows),
        "recs": recs,
    })


# --- F4: upload + my videos ------------------------------------------------------


def _require_user_page(user, next_path: str):
    """Pages that need a session redirect guests to /login?next=..."""
    if user is None:
        return RedirectResponse(f"/login?next={next_path}", status_code=303)
    return None


@router.get("/upload", response_class=HTMLResponse, include_in_schema=False)
async def upload_page(
    request: Request,
    user=Depends(videos_api._optional_user),
    settings=Depends(videos_api.get_upload_settings),
):
    redirect = _require_user_page(user, "/upload")
    if redirect is not None:
        return redirect
    return templates.TemplateResponse(request, "upload.html", {
        "request": request, "user": _public_user(user), "page": "upload", "brand": BRAND,
        "max_size": settings.max_upload_size,
        "max_size_label": f"{settings.max_upload_size // (1024 * 1024)} МБ",
    })


@router.get("/me/videos", response_class=HTMLResponse, include_in_schema=False)
async def my_videos_page(
    request: Request,
    user=Depends(videos_api._optional_user),
    video_repo=Depends(videos_api.get_video_repo),
    acl=Depends(videos_api.get_acl),
):
    redirect = _require_user_page(user, "/me/videos")
    if redirect is not None:
        return redirect
    videos = await video_repo.list_user_videos(user.id, limit=100, offset=0)
    rows = []
    for v in videos:
        entries = await acl.list_entries(v.id) if v.is_private else []
        rows.append({
            "id": v.id,
            "title": v.title,
            "status": v.status,
            "status_label": {
                "processing": "обрабатывается", "ready": "готово",
                "failed": "ошибка", "hidden": "скрыто", "deleted": "удалено",
            }.get(v.status, v.status),
            "is_private": v.is_private,
            "failure_reason": v.failure_reason,
            "view_count": v.view_count,
            "views_label": f"{v.view_count} {plural(int(v.view_count or 0), 'просмотр', 'просмотра', 'просмотров')}",
            "duration": fmt_duration(getattr(v, "duration_seconds", 0)),
            "date": time_ago(getattr(v, "created_at", None)),
            "acl": [{"user_id": u.id, "email": u.email, "channel_name": u.channel_name}
                    for u in entries],
        })
    return templates.TemplateResponse(request, "my_videos.html", {
        "request": request, "user": _public_user(user), "page": "me", "brand": BRAND,
        "rows": rows,
    })


# --- F5: channel page, subscriptions, playlist page ---------------------------


@router.get("/channel/{channel_id}", response_class=HTMLResponse, include_in_schema=False)
async def channel_page(
    request: Request,
    channel_id: uuid.UUID,
    tab: str = "videos",
    user=Depends(videos_api._optional_user),
    channel_repo=Depends(channels_api.get_channel_repo),
    sub_repo=Depends(channels_api.get_subscription_repo),
    playlist_repo=Depends(playlists_api.get_playlist_repo),
    user_repo=Depends(auth_api.get_user_repo),
):
    owner = await channel_repo.get_user(channel_id)
    if owner is None:
        raise HTTPException(404, "channel not found")
    viewer_id = user.id if user is not None else None
    subscribed = (
        await sub_repo.is_subscribed(user.id, channel_id) if user is not None else False
    )
    sub_count = await channel_repo.subscriber_count(channel_id)
    vid_count = await channel_repo.public_video_count(channel_id)

    cards: list[dict] = []
    playlist_rows: list[dict] = []
    if tab == "playlists":
        pls = await playlist_repo.list_channel_playlists(
            channel_id, viewer_id=viewer_id, limit=50, offset=0
        )
        playlist_rows = [
            {
                "id": p.id,
                "title": p.title,
                "is_private": p.is_private,
                "item_count": await playlist_repo.item_count(p.id),
                "count_label": f"{await playlist_repo.item_count(p.id)} "
                f"{plural(await playlist_repo.item_count(p.id), 'видео', 'видео', 'видео')}",
                "date": time_ago(getattr(p, "created_at", None)),
            }
            for p in pls
        ]
    else:
        vids = await channel_repo.list_channel_videos(
            channel_id, viewer_id=viewer_id, limit=50, offset=0
        )
        cards = await _feed_cards(vids, user_repo)

    return templates.TemplateResponse(request, "channel.html", {
        "request": request,
        "user": _public_user(user),
        "page": "channel",
        "brand": BRAND,
        "channel": {
            "id": owner.id,
            "channel_name": owner.channel_name,
            "description": getattr(owner, "channel_description", None) or "",
            "avatar_url": channels_api.DEFAULT_AVATAR_URL,
            "sub_count": sub_count,
            "sub_label": f"{sub_count} {plural(sub_count, 'подписчик', 'подписчика', 'подписчиков')}",
            "vid_count": vid_count,
            "vid_label": f"{vid_count} {plural(vid_count, 'видео', 'видео', 'видео')}",
            "subscribed": subscribed,
            "is_owner": user is not None and user.id == channel_id,
        },
        "tab": "playlists" if tab == "playlists" else "videos",
        "cards": cards,
        "playlists": playlist_rows,
        "empty_title": "Нет видео" if tab != "playlists" else "Нет плейлистов",
        "empty_sub": "",
    })


@router.get("/subscriptions", response_class=HTMLResponse, include_in_schema=False)
async def subscriptions_page(
    request: Request,
    user=Depends(videos_api._optional_user),
    sub_repo=Depends(channels_api.get_subscription_repo),
    user_repo=Depends(auth_api.get_user_repo),
):
    redirect = _require_user_page(user, "/subscriptions")
    if redirect is not None:
        return redirect
    channels = await sub_repo.subscribed_channels(user.id, limit=50, offset=0)
    videos = await sub_repo.subscriptions_feed(user.id, limit=24, offset=0)
    cards = await _feed_cards(videos, user_repo)
    chan_rows = [
        {"id": c.id, "channel_name": c.channel_name, "avatar_url": channels_api.DEFAULT_AVATAR_URL}
        for c in channels
    ]
    return templates.TemplateResponse(request, "subs.html", {
        "request": request,
        "user": _public_user(user),
        "page": "subs",
        "brand": BRAND,
        "channels": chan_rows,
        "cards": cards,
        "empty_title": "Нет видео от подписок",
        "empty_sub": "Подпишитесь на каналы — и их новые видео появятся здесь.",
    })


@router.get("/playlist/{playlist_id}", response_class=HTMLResponse, include_in_schema=False)
async def playlist_page(
    request: Request,
    playlist_id: uuid.UUID,
    user=Depends(videos_api._optional_user),
    playlist_repo=Depends(playlists_api.get_playlist_repo),
):
    from app.services import access
    from app.services import playlists as playlists_svc

    pl = await playlist_repo.get(playlist_id)
    if pl is None or not playlists_svc.can_view_playlist(pl, user):
        raise HTTPException(404, "not found")
    pairs = await playlist_repo.list_items_with_videos(playlist_id)
    items = [
        {"video_id": item.video_id, "title": video.title, "position": item.position}
        for item, video in pairs
        if access.can_view(video, user)
    ]
    return templates.TemplateResponse(request, "playlist.html", {
        "request": request,
        "user": _public_user(user),
        "page": "playlist",
        "brand": BRAND,
        "playlist": {
            "id": pl.id,
            "title": pl.title,
            "is_private": pl.is_private,
            "date": time_ago(getattr(pl, "created_at", None)),
            "item_count": len(items),
            "is_owner": user is not None and user.id == pl.user_id,
        },
        "items": items,
    })
