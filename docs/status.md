# Статус проекта

> Журнал «сделано / дальше / проблемы». Обновляется в конце каждой сессии.

## Сделано

- Шаг 0: `.gitignore` (коммит `0cb0aad`).
- Этап 0: проектирование — `docs/requirements.md`, `architecture.md`, `db-schema.md`, `security.md`, `adr/ADR-001..006`, `testing.md`, `plan.md`; AGENTS.md дополнен. Коммиты `0cb0aad..ef35b71`.
- Правки по ревью Этапа 0 (m00155): ответы на 5 вопросов в `requirements.md` (раздел 7), приоритеты P0/P1/P2 в `plan.md`, presigned host за reverse proxy в `architecture.md` + ADR-002/003, статус `deleted` в `db-schema.md`, правило целостности файлов в AGENTS.md, журнал `status.md`. Коммиты `5fc6ba6`, `5bc70cb`, `cb995f4`.
- Этап 1 (каркас): 1.1 uv-проект, Python 3.12.13, зависимости, pytest-конфиг (`3e95be2`); 1.2 FastAPI-каркас: `create_app`, `/health`, `/docs`, `core/config.py` (Settings с fail-fast валидацией секрета в production), `core/db.py` (async engine/session) (`de84d0a`); 1.3 Alembic: async `env.py`, URL из окружения, пустая baseline-миграция `2363426a9ef1` (`263fdf1`); 1.4 Dockerfile (uv + ffmpeg, один образ web/worker), docker-compose.yml (postgres/redis/minio/web/worker/nginx + profile seed, порты публикует только nginx), nginx.conf (/videos/, /thumbnails/ → MinIO c Host $host; /media/thumbnails/ rewrite; client_max_body_size 512m) — НЕ запускались (`33477a4`); `.env.example` (`4b019c2`).
- Тесты: 13 unit, все зелёные, `uv run ruff check .` чист.

## Дальше

- Этап 2 (вертикальный срез, `docs/plan.md` 2.1–2.15): users+sessions, регистрация, логин/логаут, videos+video_views, storage-интерфейс, POST /api/videos, воркер transcode, страница просмотра, дедуп просмотров, реакции, комментарии, лента.
- До старта этапа 2: ручная проверка владельцем compose-стека (см. резюме сессии).

## Проблемы

- Инструмент записи больших файлов периодически портит содержимое (обрывки черновиков, посторонние инструкции-инъекции). Случаи доложены владельцу; правило «перечитывать файл после записи» закреплено в AGENTS.md.
- Мелочь: `uv init` записал `requires-python >=3.14` — исправлено на `>=3.12` вручную, venv пересоздан.
