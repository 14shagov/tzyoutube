"""SSR pages (stage 3 UI, ADR-007): Jinja2 templates + small external JS.

All HTML routes live in this module; the JSON API stays in app/api/*. Page
handlers reuse the SAME dependency providers as the API modules, so tests
override them once and both API and pages see the fakes.

Rules (owner spec m02359): no external CDN/fonts, no inline <script> or
on*= handlers (CSP script-src 'self'), all JS in /static/*.js, Jinja
autoescape on and NO |safe for user data anywhere.
"""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api import auth as auth_api
from app.api import videos as videos_api

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
