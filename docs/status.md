# Статус проекта

Обновлено: 2026-11-12 (после ревью m01611 этапа 2 — исправления 6 задач).

## Сломано на реальном стеке и почему юнит-тесты не ловили (m01611)

- web: `alembic upgrade head` падал `sqlalchemy.exc.ArgumentError:
  Column must be constructed with a non-blank name...` — хелпер `_uuid()`
  в миграции b7c8d9e0f1a2 создавал Column без имени. Юнит-тесты не ловили:
  они проверяли маркеры в тексте миграции, но ни разу не ВЫПОЛНЯЛИ
  миграции. Лечение: offline-страж `alembic upgrade head --sql` без БД
  (tests/test_migrations_offline.py), реально исполняющий все миграции.
- worker: `RuntimeError: at least one function or cron_job must be
  registered` — WorkerSettings был стабом с functions=[]. Юнит-тесты не
  ловили: очередь в части B тестировалась на фейках, воркер ни разу не
  импортировался тестами. Лечение: страж tests/test_worker_wiring.py
  (registered ⊇ enqueued, ctx+1 позиционный аргумент, RedisSettings).
- Оба класса ошибок — «интеграция на реальном стеке», теперь прикрыты
  статическими стражами, исполняющими код без Docker.

## Исправления по ревью m01611 (по одному коммиту)

- 379c13b fix(migrations): _uuid() именует колонку "id"; страж offline-DDL
  (негативно проверен на сломанном состоянии).
- (worker) регистрация transcode в WorkerSettings: адаптеры _TaskRepo
  (per-call throwaway engine — asyncpg loop-bound), _TaskStorage (minio
  fget/fput), _ffmpeg_runner/_duration_probe, asyncio.to_thread; страж
  согласованности очереди (негативно проверен).
- (models-guard) tests/test_models_match_migrations.py: имена таблиц и
  колонок ORM ↔ offline DDL (негативно проверен инъекцией лишней колонки).
- (manual-checks) пункт 11: миграция на пустой БД + логи web/worker.
- (security/csrf) CsrfMiddleware: same-origin для POST/PUT/PATCH/DELETE,
  Origin|Referer, дефолтные порты нормализованы; 7 тестов.
- (security/cookies+ip) session cookie Secure в production; rate-limit
  client key ТОЛЬКО из X-Real-IP (nginx перезаписывает $remote_addr;
  контракт закреплён комментарием в nginx.conf), спуфинг XFF больше не
  влияет; 4 теста.

## Сделано

- Этап 0: проектирование — принято ревьюером.
- Этап 1: каркас — принят, Docker проверен вручную без override.
- Этап 2, части A–D (теги stage2-part-a..d): auth+сессии+роли+rate limit;
  загрузка+валидация+очередь; сервис транскода+access-контроль+presigned;
  карточка/лента/поиск/просмотры/реакции/комментарии.
- Итог после исправлений: 112 тестов зелёные, ruff чист.

## Не сделано (сознательно)

- Шаблоны и фронтенд (по заданию владельца — позже).
- Сервисы каналов/подписок/плейлистов/жалоб/модерации (этапы 3–4).
- Воркер-задача cleanup_video (удаление — 3.7), seed и deploy.sh (этап 5).

## Проблемы и заметки

- Инциденты порчи больших файлов ранее — правило перечитывания соблюдено;
  в этом прогоне новых инцидентов не было.
- Alembic autogenerate недоступен без локальной PostgreSQL: миграции
  вручную + стражи (структурные маркеры, offline DDL, models↔migrations).
- passlib убран (несовместим с bcrypt 5.x) — допущение 10.

## Дальше

- Ручные проверки владельцем (docs/manual-checks.md, пункт 11 — ключевой:
  web/worker не в цикле перезапусков, миграция на пустой БД).
- Затем этап 3 (каналы, подписки, приватность UI, плейлисты, удаление).
