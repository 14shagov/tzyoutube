# Статус проекта

Обновлено: 2026-11-14 (ревью, группы A и B закрыты; юнит-прогон 223 passed).

## Ускорение юнит-прогона (задания владельца, по одному коммиту)

- Профиль до исправлений: **141 passed in 1697s (28:17)** — подробно
  в разделе «Профиль…» ниже. Причина подтверждена: lifespan →
  `get_storage().ensure_buckets()` → реальный silo вне Docker, ~30.6s
  на каждый вход в `with TestClient(app)`, ~55 входов.
- `test(conftest)`: autouse-заглушка — `app.main.get_storage` →
  `Storage(autospec(Minio, instance=True))`, buckets «существуют»;
  `storage_module.get_storage` не тронут (проверка presign-клиентов
  осталась реальной). bcrypt cost 12 → 4 только в тестах. Мета-тесты
  в test_isolated_environment.py падают, если патчи исчезнут.
- `test(auth)`: страж production cost 12 — читает исходник auth.py
  через AST (иммунно к monkeypatch): константа = 12, и hash_password
  её реально использует.
- `test(storage)`: TestEnsureBuckets теперь проверяет точную
  последовательность bucket_exists/make_bucket (autospec против
  реального Minio), остаётся самостоятельным — строит свой Storage и
  не зависит от заглушки conftest.
- `test(docker)`: test_copy_sources… skip с явной причиной без .git.
- `chore(pytest)`: pytest-timeout 60s/тест (method=thread — Windows
  без SIGALRM); проверен пробником sleep(70) — упал ровно на 60s.
- Побочно: test_session_expiry_is_enforced теперь истекает сессию из
  cookie клиента — при быстром хешировании created_at обеих сессий
  ложатся в один тик часов, выбор «новейшей» по created_at стал гоночным.
- **Итог: 145 passed in 11.1s**, ruff чист. Замедления разложились:
  3 теста alembic offline-DDL ~1.5s (тolerable), остальное < 0.2s.

## Профиль полного юнит-прогона (замер до исправлений)

- `uv run pytest -q --durations=15`: **141 passed in 1697s (28:17)**, без
  Docker, хранилище недоступно. Прогон «зависает» не намертво — каждый
  вызов отпускает, но через десятки секунд.
- Топ-15 медленных: 30.6–32.0s каждый, все — входы в lifespan
  (`with TestClient(app)`), и setup, и call. 1697 / 30.9 ≈ 55 входов —
  совпадает с числом тестов, открывающих клиент через context manager
  (8 файлов через фикстуру + test_cookie_secure_setting.py входит
  `with`-ом прямо в теле тестов — 31s × 2).
- Причина подтверждена: lifespan → `get_storage().ensure_buckets()` →
  реальный Minio на localhost:9000. Вне Docker хранилища нет:
  `bucket_exists` по 3 бакетам ждёт таймауты/ретраи ≈ 10s на бакет,
  ~30.6s на каждый вход в lifespan. Итог: 55 × 30.9s ≈ 28 минут.
- bcrypt cost 12 — вторичная причина: ~0.3s на каждый register/login.
  В топ-15 не виден (маскируется 30-секундными входами), после
  устранения основной станет заметен.
- Файлы без lifespan (test_app.py — AsyncClient+ASGITransport,
  test_csrf_proxy_chain.py — прямой вызов middleware) — быстрые,
  подтверждает локализацию.


## Сломано на реальном стеке и почему юнит-тесты не ловили (m02086)

- POST /api/videos → 500 `TypeError: Minio.put_object() missing 1
  required positional argument: 'length'` — обёртка storage.put звала
  put_object без обязательного length; фейк в тестах повторял ту же
  ошибку (выдуманная сигнатура). Лечение: все storage-тесты переведены
  на create_autospec(Minio) — реальная библиотечная сигнатура; обёртка
  исправлена (put: BytesIO+len; put_stream: файл+len+part_size 16МБ).
- Попутно найдено и закрыто: ensure_buckets НЕ существовал — бакеты
  никто не создавал, на реальном стеке упало бы NoSuchBucket.
- Presigned-ссылки подписывались внутренним клиентом (silo:9000) —
  в браузере не открылись бы. Теперь отдельный presign-клиент на
  S3_PUBLIC_ENDPOINT (по умолчанию localhost, порт 80); nginx во всех
  silo-locations передаёт Host как $http_host (подпись привязана к
  Host). Подпись офлайн — сетевого обращения нет.
- Регистрация не открывала сессию: сценарий «регистрация → загрузка»
  требовал лишний login. Теперь register сразу ставит cookie (выбор
  задокументирован как FR-6.5).

## Исправления по ревью m02086 (по одному коммиту)

- test(storage): pin wrapper to real minio signatures via autospec
  (воспроизведение — красный до фикса).
- fix(storage): real minio signatures, streaming upload (temp-файл,
  лимит по мере чтения, MinIO-вызовы через asyncio.to_thread),
  ensure_buckets; аудит всех вызовов: put/put_stream/fput/fget/
  presigned/remove/bucket_exists/make_bucket — все сверены с minio
  7.2.20; worker fget/fput ок; фейки в тестах → autospec.
- feat(storage): presign против публичного endpoint + nginx Host
  $http_host + README (S3_PUBLIC_ENDPOINT на VM) + .env.example.
- feat(auth): register сразу открывает сессию (FR-6.5) + тесты
  (register → me, register → upload без login); follow-up: фейк
  create_session теперь проставляет created_at (ловилось только
  в полном прогоне — max() по None).

## Сделано

- Этап 0: проектирование — принято. Этап 1: каркас — принят.
- Этап 2 A–D (stage2-part-a..d) + ревью m01611 (6 задач) + m01882
  (CSRF за nginx) + m02086 (этот прогон).
- Ускорение юнит-прогона 2026-11-13 (см. раздел выше): 145 тестов,
  11 секунд вместо 28 минут, ruff чист.

## Не сделано (сознательно)

- Шаблоны и фронтенд (по заданию владельца — позже).
- Каналы/подписки/плейлисты/жалобы/модерация (этапы 3–4), cleanup_video
  (3.7), seed и deploy.sh (этап 5).

## Проблемы и заметки

- Порядок «тесты → коммит» нарушен один раз (закоммичен падающий
  register-тест до фикса фейка) — пойман сразу же, follow-up коммит
  в тот же прогон; впредь проверять прогон ПЕРЕД git commit, а не после.
- _TaskStorage в воркере дублирует клиент get_storage()._s3 вместо
  методов Storage — не баг (сигнатуры верны), но кандидать на
  рефакторинг в этапе 3.
- Alembic autogenerate недоступен без локальной PostgreSQL: миграции
  вручную + стражи. passlib убран (допущение 10).

## Дальше

- Ручные проверки: manual-checks.md п.14 (ключевая — вся цепочка
  загрузки: 202 → processing → ready → play_url открывается), п.12-13.
- Затем этап 3 (каналы, подписки, приватность, плейлисты, удаление).

## Ревью (независимый список замечаний), группа A — сделано 2026-11-13

По одному коммиту, каждый с красным тестом перед фиксом; после группы
`uv run pytest -q` = 178 passed, `uv run ruff check .` чист.

- A1 `4f90a22`: провайдеры `get_feed_repo/get_view_repo/get_reaction_repo/
  get_comment_repo/get_acl` создавали SQL-репозитории без сессии → TypeError
  → 500 на всех viewing-эндпоинтах. Теперь берут
  `session: AsyncSession = Depends(get_db_session)`. Страж
  `tests/test_dependency_guard.py` обходит все 14 APIRoute (в FastAPI 0.162
  include_router прячет маршруты в `_IncludedRouter.original_router`) и
  резолвит зависимости с переопределением только сессии и внешних клиентов.
- A2 `b29a4e1`: `SqlViewRepo.recent_view` возвращает bool, а сервис проверял
  `is not None` → просмотр никогда не засчитывался. Контракт приведён к
  bool (`if await repo.recent_view(...)`), протокол `-> bool`; тесты: первый
  просмотр засчитан, повтор в 24 ч нет, после окна снова да.
- A3 `e2c1a5c`: эндпоинт `/view` передавал `user.id` (UUID), сервис читал
  `user.id` → AttributeError → 500 у залогиненных. Теперь передаётся объект
  user (None для гостя); тест с реальным `app.models.User`.
- A4 `f4c53a0`: `SqlReactionRepo.get` возвращал объект Reaction вместо
  `int | None` по протоколу → `current == value` всегда False, повторный
  лайк не снимался. `get` возвращает `row.value`, upsert обновляет через
  `_get_obj`. Сценарии: лайк / повтор (снятие) / лайк→дизлайк / 0 (снятие) /
  счётчики.
- A5+A6 `8a08ea1`: `CommentIn.parent_id`/`CommentOut.id,parent_id` были `int`,
  а `Comment.id` — PGUUID → ValidationError → 500. Схемы переведены на
  `uuid.UUID`. Аудит остальных контрактов (A6): протоколы UserRepo /
  AclRepo / RateLimiter / Storage / Queue / VideoCard / FeedItem / VideoOut /
  ReactionOut согласованы с реализациями; единственное найденное
  рассогласование — `SqlCommentRepo.add` бросает ValueError("invalid
  parent_id"), теперь маппится в 422 (а не 500) на эндпоинте.

### Ручные проверки после группы A (владелец, Docker) — manual-checks.md

- п.8 (ключевая для группы A): лента/поиск/view → 204, реакция
  `{"likes":1,"dislikes":0}` и повторный POST `{"value":1}` →
  `{"likes":0,"dislikes":0}` (A4), комментарий 201 с UUID `id` и есть в
  списке (A5); повторный view в течение 24 ч не увеличивает `view_count`
  (A2 — проверить SELECT по video_views/view_count).
- п.6-7, п.14: цепочка загрузки и play_url (регресс-проверка, A1 мог
  задеть card/feed).
- п.5, п.12-13: auth/CSRF без регрессий.

### Дальше

- Группа B (B1-B7) — только по команде владельца после паузы.

## Ревью (независимый список замечаний), группа B — сделано 2026-11-14

По одному коммиту, каждый с красным тестом перед фиксом; после группы
`uv run pytest -q` = 223 passed, `uv run ruff check .` чист.

- B1 `67b6870`: `limit`/`offset` на `/api/feed`, `/api/search`,
  `/api/videos/{id}/comments` были без границ → неограниченный
  LIMIT/OFFSET (DoS). Теперь `Query(ge=0, le=100)` для limit и `ge=0`
  для offset: fuori range → 422, границы 0/100 принимаются.
- B2 `f2f4c98`: `/auth/register` не имел rate limit, а bcrypt cost 12
  на каждый запрос — CPU-DoS. Теперь 5 регистраций / 10 мин на X-Real-IP
  (тот же ключ, что у login; X-Forwarded-For не учитывается), счётчик
  бьёт ДО хеширования пароля; 6-й запрос → 429 до создания пользователя.
- B3 `a8f5844`: лимит логина считал ВСЕ попытки (успешный вход тратил
  бюджет) и для чужого email не было bcrypt-проверки (enumeration по
  таймингу). Теперь: только неудачные попытки считаются (hit), успешный
  вход — нет; лимит сначала проверяется peek-методом `check` (6-я попытка
  → 429 даже с верным паролем, до любого хеширования); чужой email —
  dummy-верификация против process-cached хеша той же стоимости.
- B4 `76b27f3`: title > 200 символов уходил в БД (String(200)) → 500;
  guest_id cookie > 64 символов — то же самое в String(64). Теперь
  `Form(max_length=200)` и `Cookie(max_length=64)` → 422; guest_id вне
  charset `[A-Za-z0-9_-]{1,64}` заменяется свежим `token_urlsafe(24)`.
- B5 `a4ac5aa`: logout с «мусорным» cookie падал в `uuid.UUID(...)` →
  500. Теперь идемпотентно: невалидный UUID = «нечего удалять» → 204 +
  cookie очищается; 401 только при отсутствии cookie.
- B6 `af74784`: поиск `ilike(f"%{query}%")` без экранирования —
  `q=100%` находил всё. Теперь `escape_ilike` (сначала `\`, потом `%`,
  `_`) + `escape="\\"` в самом LIKE; тесты смотрят bind-параметр и
  наличие ESCAPE в скомпилированном SQL.
- B7 `597e3ed`: протухшие сессии копились вечно. Теперь
  `purge_expired_sessions()` в протоколе UserRepo (Sql + Fake) и вызывается
  при УСПЕШНОМ логине (неудачный не пишет); миграция уже индексировала
  `sessions.expires_at`.

### Ручные проверки после группы B (владелец, Docker) — manual-checks.md

- п.5: 6-я регистрация с одного IP → 429; 5 неудачных login → 6-й (даже
  с верным паролем) → 429; 8 успешных login подряд → все 200.
- п.8: `search?q=100%25` не возвращает все видео; view с cookie
  `guest_id` длиннее 64 символов → 422, с «кривыми» символами → 204,
  в `video_views.viewer_fingerprint` лежит короткий валидный токен;
  `limit=101` на ленте/поиске/комментариях → 422.
- п.14: upload с title в 201 символ → 422 (не 500).
- logout с cookie `session_id=garbage` → 204.
- B7: после нескольких входов `SELECT count(*) FROM sessions` —
  протухшие строки удалены.

### Дальше

- Группа C (C1-C5) — только по команде владельца после паузы.
