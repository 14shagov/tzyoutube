# Статус проекта

Обновлено: 2026-11-12 (после Этапа 2, части A–D).

## Сделано

- Этап 0: проектирование (requirements, architecture, db-schema, security,
  ADR-001..006, testing, plan) — принято ревьюером.
- Этап 1: каркас (uv, FastAPI /health + /docs, Settings, alembic, Dockerfile,
  compose, статические стражи) — принят ревьюером, ручная проверка Docker
  пройдена без override-файла.
- Этап 2, часть A (tag stage2-part-a, eeab8de): модели users/sessions +
  миграция a1b2c3d4e5f6, bcrypt (cost 12) без passlib, регистрация/вход/
  выход, сессии HttpOnly SameSite=Lax cookie, get_current_user,
  require_role, rate limit на вход (5/10 мин, in-memory в дев-режиме,
  Redis в production), тесты безопасности (дубликаты, generic 401,
  блокировка, 429, роль 403).
- Этап 2, часть B (tag stage2-part-b, 9bd1673): storage-интерфейс S3
  (put/presigned/delete), валидация загрузки (расширение + magic bytes +
  размер из env), POST /api/videos (multipart), raw-ключи, очередь
  arq-заглушка enqueue_transcode.
- Этап 2, часть C (tag stage2-part-c, 3468f7c): сервис транскода (ffmpeg
  480p + превью, синхронный, изолированный runner), access-контроль
  (can_view, can_view_with_acl, view_or_none), presigned GET c TTL из env.
- Этап 2, часть D (tag stage2-part-d, 00fcf83): модели video_views/
  reactions/comments/video_acl + миграция b7c8d9e0f1a2, SqlFeedRepo/
  SqlViewRepo/SqlReactionRepo/SqlCommentRepo/SqlAclRepo, эндпоинты:
  карточка GET /api/videos/{id}, лента GET /api/feed, поиск
  GET /api/search?q=, просмотры POST /api/videos/{id}/view (дедуп 24 ч,
  гости по cookie guest_id), реакции POST .../reactions, комментарии
  POST/GET .../comments (1 уровень, trim, 1..2000).
- Итог: 95 тестов зелёные (uv run pytest), ruff чист.

## Не сделано (сознательно, вне объёма этапа 2 бэкенда)

- Шаблоны и фронтенд (по заданию владельца — позже).
- Сервисы каналов/подписок/плейлистов/жалоб/модерации (этапы 3–4 плана).
- Воркер-задача cleanup_video (удаление — задача 3.7).
- seed и deploy.sh (этап 5).

## Проблемы и заметки

- Инциденты порчи вывода при записи больших файлов (ранее) — правило
  перечитывания соблюдено в этом этапе; в конце этапа замечен одиночный
  артефакт генерации в собственном сообщении агента (битая строка даты),
  содержимого файлов не касался, юнит-тестов/файлов не поразил.
- Alembic autogenerate недоступен без локальной PostgreSQL: миграции
  пишутся вручную и накрываются тестами-стражами (структурные маркеры).
- passlib убран (несовместим с bcrypt 5.x) — допущение 10 в
  docs/requirements.md.

## Дальше

- Этап 3 (каналы, подписки, приватность UI, плейлисты, удаление) —
  после подтверждения владельцем результатов этапа 2.
