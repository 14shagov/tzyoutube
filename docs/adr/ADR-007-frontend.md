# ADR-007: Frontend — SSR Jinja2 + small external JS, no bundler, no CDN

Status: accepted (2026-11-15). Context: owner task m02359 — simple frontend that
looks and lays out like YouTube (graded on similarity), deployed on a clean VM
without extra dependencies; own name/logo, no trademark copying.

## Decision

Server-side rendering with **Jinja2 inside FastAPI** (`src/app/pages.py` +
`src/app/templates/*.html`), plus a small amount of **vanilla JS in external
files** under `src/app/static/*.js` (served same-origin via `app.mount("/static")`).
No build step, no bundler, no frontend framework.

Consequences / rules:

1. **No external CDN, fonts, or scripts.** All static assets, icons (inline
   SVG in templates), and the system font stack live in the repo. A guard test
   (`tests/test_frontend_f7.py::test_no_external_urls_in_templates_and_static`)
   fails the build if any `http(s)://` URL other than localhost appears in
   templates or static files.
2. **CSP-ready**: no inline `<script>` and no `on*=` handlers anywhere (guard
   test included); every JS file is external, so nginx sends
   `Content-Security-Policy: default-src 'self'; script-src 'self'; ...`
   (media-src also allows `$CSP_MEDIA_ORIGIN` for presigned video URLs).
3. **No `|safe`** for user data — Jinja autoescape stays on; XSS tests pin
   escaping of video titles, descriptions, channel names, comment text.
4. **JS talks to the API same-origin** via `fetch` (`window.tzApi` in
   `app.js`); the session cookie rides along; API error `detail` strings are
   shown to the user in Russian in a toast/error box. XHR is used only for
   upload progress events (`upload.js`).
5. **Themes**: CSS custom properties in `app.css`; light default,
   `[data-theme="dark"]` override, `@media (prefers-color-scheme: dark)`
   fallback; `theme.js` (loaded in `<head>`) sets `data-theme` from
   localStorage or the media query before first paint; header toggle button
   flips it.
6. **Layout** (YouTube-like): sticky top bar (menu button, logo, search box,
   upload button, theme toggle, avatar/login), collapsible left sidebar
   (Главная, Подписки, Вы, + Модерация for staff), responsive grid of 16:9
   cards (thumbnail, duration badge, avatar, title, channel, views·date),
   chips row on the home page. Watch page: two columns — player + title +
   channel row with subscribe button + like/dislike segmented control +
   description + comments; right column recommendations. Channel page: header
   (avatar, name, counters, description, subscribe/edit) + tabs
   «Видео»/«Плейлисты».
7. **HTML error pages** (`error.html`) for 403/404/500 on browser routes via
   exception handlers in `main.py`; `/api/*`, `/auth/*`, `/docs` and JSON
   clients keep JSON error bodies.
8. **Branding**: own name "TZ Video" with a simple red play-button SVG logo;
   no YouTube trademarks, names, or assets.

## Alternatives considered

- SPA (React/Vue) + bundler: rejected — needs a build step and node_modules at
  deploy time; violates "clean VM, no extra deps" and complicates CSP.
- CDN fonts/icons (e.g. Google Fonts): rejected — external requests break on a
  clean VM and violate the no-external-URL rule.
- Inline scripts: rejected — blocks `script-src 'self'`.

## Pages & JS modules

| Page | Route | Template | JS |
|---|---|---|---|
| Home feed | `/` | home.html (+_cards.html) | app.js |
| Search | `/search?q=` | home.html | app.js |
| Login/Register | `/login`, `/register` | auth.html | auth.js |
| Watch | `/watch/{id}` | watch.html | watch.js |
| Upload | `/upload` | upload.html | upload.js |
| My videos | `/me/videos` | my_videos.html | manage.js |
| Channel | `/channel/{id}?tab=` | channel.html (+_cards.html) | channel.js |
| Subscriptions | `/subscriptions` | subs.html | — |
| Playlist | `/playlist/{id}` | playlist.html | playlist.js |
| Moderation | `/mod` | mod.html | mod.js |
| Errors | any | error.html | — |
