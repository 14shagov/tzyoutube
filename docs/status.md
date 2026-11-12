# Статус проекта

Обновлено: 2026-11-12 (после ревью m01882 — CSRF за nginx).

## Сломано на реальном стеке и почему юнит-тесты не ловили (m01882)

- ВСЕ изменяющие запросы из Swagger за nginx → 403 cross-origin.
  Причина: CsrfMiddleware сравнивал Origin с scope["server"] — а это
  адрес СОКЕТА uvicorn в контейнере (172.21.0.x:8000), а не публичный
  хост. Браузер шлёт Origin: http://localhost — сравнение всегда ложно.
  Юнит-тесты не ловили: TestClient подставляет согласованные
  server/Host; реальной цепочки «nginx → контейнер» в тестах не было.
  Отказы не логировались — в `docker compose logs web` видны были
  только access-строки с 403.
- Лечение: источник правды — ALLOWED_ORIGINS из env (по умолчанию
  http://localhost,http://127.0.0.1) + Host-заголовок как fallback;
  scope["server"] больше не используется. nginx передаёт Host как
  $http_host (с портом) + X-Forwarded-Proto $scheme. Каждый отказ
  логируется WARNING с причиной (origin, referer, allowed-список) —
  тест на наличие записи.
- Воспроизведение (тест): ручной ASGI-scope с server=("172.21.0.5",
  8000), Host: localhost, Origin: http://localhost — был красным до
  фикса, зелёный после (tests/test_csrf_proxy_chain.py).

## Исправления по ревью m01882 (по одному коммиту)

- 5c13782/f230aaa test(security): воспроизведение цепочки nginx→контейнер.
- 56fea54 fix(security): CSRF origin через ALLOWED_ORIGINS + Host
  fallback; nginx $http_host + X-Forwarded-Proto; .env.example;
  README (как задать origin на VM: IP или домен, для деплоя одной
  командой); тесты: свой origin проходит, чужой отклонён, без Origin
  (curl) проходит, Origin: null отклонён, Referer проверяется так же,
  Swagger-цепочка проходит, GET/HEAD не затрагиваются, нормализация
  портов 80/443.
- (csrf-logging) тест: отказ = WARNING c origin/path/allowed, без
  cookie/секретов.
- (cookie-secure) COOKIE_SECURE отдельной настройкой (по умолчанию
  false): production по plain HTTP обязан работать; compose передаёт
  ALLOWED_ORIGINS/COOKIE_SECURE в web; README + docs/security.md
  описывают, когда включать (только при HTTPS). Тесты на обе ветки.
- (manual-checks) пункты 12–13: Swagger-регистрация за nginx проходит;
  curl с Origin localhost → 422; Origin evil.example → 403 + WARNING
  в логах web.

## Сделано

- Этап 0: проектирование — принято ревьюером.
- Этап 1: каркас — принят, Docker проверен вручную без override.
- Этап 2, части A–D (теги stage2-part-a..d): auth+сессии+роли+rate limit;
  загрузка+валидация+очередь; сервис транскода+access-контроль+presigned;
  карточка/лента/поиск/просмотры/реакции/комментарии.
- Ревью m01611: 6 задач (миграции _uuid, воркер transcode+стражи,
  models↔migrations, manual-checks п.11, CSRF same-origin, Secure
  cookie/X-Real-IP).
- Ревью m01882: CSRF за nginx (этот прогон).
- Итог: 127 тестов зелёные, ruff чист.

## Не сделано (сознательно)

- Шаблоны и фронтенд (по заданию владельца — позже).
- Сервисы каналов/подписок/плейлистов/жалоб/модерации (этапы 3–4).
- Воркер-задача cleanup_video (удаление — 3.7), seed и deploy.sh (этап 5).

## Проблемы и заметки

- Инциденты порчи больших файлов ранее — правило перечитывания соблюдено;
  в этом прогоне новых инцидентов не было (regex-патч теста заменён
  ручной перезаписью файла).
- Alembic autogenerate недоступен без локальной PostgreSQL: миграции
  вручную + стражи (структурные маркеры, offline DDL, models↔migrations).
- passlib убран (несовместим с bcrypt 5.x) — допущение 10.

## Дальше

- Ручные проверки владельцем: docs/manual-checks.md, ключевые — п.11
  (миграция на пустой БД), п.12 (Swagger за nginx), п.13 (CSRF+WARNING).
- Затем этап 3 (каналы, подписки, приватность UI, плейлисты, удаление).
