# Статус проекта

Обновлено: 2026-11-12 (после ревью m02086 — загрузка 500, presigned, регистрация-сессия).

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
- Итог: 139 тестов зелёные, ruff чист.

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
