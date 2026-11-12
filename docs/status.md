# Статус проекта

> Журнал «сделано / дальше / проблемы». Обновляется в конце каждой сессии.

## Сделано

- Шаг 0: `.gitignore` (коммит `0cb0aad`).
- Этап 0: проектирование — `docs/requirements.md`, `architecture.md`, `db-schema.md`, `security.md`, `adr/ADR-001..006`, `testing.md`, `plan.md`; AGENTS.md дополнен. Коммиты `0cb0aad..ef35b71`.
- Правки по ревью Этапа 0 (m00155): ответы на 5 вопросов в `requirements.md` (раздел 7), приоритеты P0/P1/P2 в `plan.md`, presigned host за reverse proxy в `architecture.md` + ADR-002/003, статус `deleted` в `db-schema.md`, правило целостности файлов в AGENTS.md, журнал `status.md`. Коммиты `5fc6ba6`, `5bc70cb`, `cb995f4`.
- Этап 1 (каркас): 1.1 uv-проект, Python 3.12.13, зависимости, pytest-конфиг (`3e95be2`); 1.2 FastAPI-каркас (`de84d0a`); 1.3 Alembic async + baseline (`263fdf1`); 1.4 Dockerfile/compose/nginx заготовки (`33477a4`); `.env.example` (`4b019c2`); статус (`bdf8bf8`).
- Ревью Docker владельцем (m00687) и исправления, по одному коммиту на задачу:
  1. `777f067` — образ `pgsty/silo:RELEASE.2026-09-16T00-00-00Z` вместо удалённого `minio/minio`; сервис/volume переименованы в `silo`; ADR-001 пересмотрен (MinIO archived, варианты Silo/RustFS/SeaweedFS/Garage, риски AGPLv3 и best-effort mc), architecture/db-schema/security/testing/ADR-003/005/006 обновлены; AGENTS.md — правило версионированных тегов (`44be4c8`).
  2. `bcc7ea3` — единый корень импорта: `ENV PYTHONPATH=/srv/app/src` в Dockerfile; команды web/alembic/seed — `app.*`, воркер — `worker.main.WorkerSettings` (заготовка `src/worker/main.py` создана); никаких `src.*` и локальных обходов.
  3. `1965dab` — зависимости ставятся при сборке (`uv sync --frozen --no-install-project --no-dev`), рантайм — `uv run --no-sync` (без докачки dev-пакетов и пересинхронизации), uv запинен `ghcr.io/astral-sh/uv:0.11.11`, слой deps раньше кода (кэш).
  4. `fbb0e78` — статические тесты-стражи: запрет `:latest` и образов minio/minio, minio/mc, quay.io/minio; единый корень импорта в командах; `uv run` только с `--no-sync`; `--no-dev` в сборке; порядок слоёв. Проверены негативно (ловят подмену тега на latest).
- Тесты: 19 unit, все зелёные; `uv run ruff check .` чист.
- Этап 2, часть A (пользователи и сессии): модели User/SessionToken/Video/VideoView в `src/app/models.py`; сервис `app/services/auth.py` (bcrypt cost 12 напрямую, без passlib — тот несовместим с bcrypt 5.x; регистрация/логин/resolve_session, доменные ошибки); `app/services/rate_limit.py` (in-memory для юнит-режима, Redis для production); `app/services/fakes.py` (FakeUserRepo); API `app/api/auth.py` (register/login/logout/me, cookie HttpOnly SameSite=Lax, generic 401); `app/api/deps.py` (require_role); миграция `a1b2c3d4e5f6` users+sessions (вручную — autogenerate требует живую БД); ruff-конфиг: `extend-immutable-calls` для Depends/require_role (B008). 38 unit-тестов зелёные, ruff чист.
- Этап 2, часть B (загрузка видео): `app/core/storage.py` (Storage-обёртка над S3-клиентом, BUCKETS raw/videos/thumbnails — ADR-001, ленивый singleton get_storage); `app/services/videos.py` (validate_upload: whitelist расширений + magic bytes ftyp/EBML/RIFF-AVI + размер из MAX_UPLOAD_SIZE, UploadArgs, VideoStatus, new_video/build_raw_key); `app/api/videos.py` (POST /api/videos multipart: Form-поля title/description + File, 401 без сессии, 415 неверный тип/контент, 413 оверсайз, 202 + статус processing, стрим в raw/<uuid>.<ext>, enqueue transcode); `app/worker/queue.py` (ArqTranscodeQueue поверх arq create_pool); зависимость python-multipart. 62 теста зелёные, ruff чист.

## Проверено вручную (владельцем, Docker Desktop Windows)

- Стек поднимался с локальным override-файлом (в репозиторий не входит): postgres, redis, silo, web, nginx; `pgsty/silo:RELEASE.2026-09-16T00-00-00Z` здоров, бакет `videos` создаётся, `/videos/*` отдаёт AccessDenied без подписи.
- Проблемы, найденные в ревью (удалённый образ minio, ModuleNotFoundError без PYTHONPATH, докачка dev-акетов при старте, uv :latest), исправлены — см. коммиты выше.
- Повторная проверка БЕЗ override вскрыла падение сборки: `COPY scripts ./scripts` — каталог `scripts/` отсутствовал в репо (seed планировался на этап 5). Исправлено заглушкой `scripts/seed.py` (`0e398bc`: честный отказ с exit=1, без имитации успеха) + статический страж COPY/ADD-источников (`64b91f8`: каждый источник должен существовать и быть git-tracked; негативно проверен — падал до исправления). Попутно исправлена опечатка `set .env` → `set in .env` в seed-сервисе compose.

## НЕ проверено вручную (нужно после этих правок)

- Полный стек БЕЗ локального override-файла (команды — в резюме сессии): build с нуля, health, миграции при старте web, worker-stub, раздача `/videos/test` → 403.
- `docker compose --profile seed run --rm seed` — заглушка честно падает «seed not implemented yet» (exit=1); реальный seed — этап 5.
- deploy.sh на чистой Ubuntu — этап 5.

## Дальше

- Этап 2 (вертикальный срез, `docs/plan.md` 2.1–2.15): users+sessions, регистрация, логин/логаут, videos+video_views, storage-интерфейс, POST /api/videos, воркер transcode, страница просмотра, дедуп просмотров, реакции, комментарии, лента.
- До старта этапа 2: ручная проверка владельцем стека без override (см. выше).

## Проблемы

- Инструмент записи больших файлов периодически портит содержимое (обрывки черновиков, посторонние инструкции-инъекции). Случаи доложены владельцу; правило «перечитывать файл после записи» закреплено в AGENTS.md. В этой сессии — чисто.
- `uv init` записал `requires-python >=3.14` — исправлено на `>=3.12` вручную (этап 1).
