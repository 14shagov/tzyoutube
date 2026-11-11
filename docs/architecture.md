# Архитектура

> Стек зафиксирован ТЗ: Python + FastAPI, PostgreSQL, Swagger на `/docs`, простой фронт,
> Docker Compose, деплой одной командой на чистой Ubuntu.
> Остальные решения обоснованы в `docs/adr/`.

## 1. Компоненты

| Компонент | Технология | Ответственность |
|---|---|---|
| API + SSR | FastAPI (uvicorn), Jinja2-шаблоны | REST API, серверный рендеринг экранов, авторизация, проверка прав, выдача подписанных URL |
| БД | PostgreSQL 16 | users, videos, comments, likes, subscriptions, playlists, reports, sessions |
| Объектное хранилище | MinIO (S3-совместимое) | Исходники, готовые транскоды, превью |
| Фоновый воркер | arq (очередь на Redis 7) | Транскод ffmpeg, генерация превью |
| Reverse proxy | NGINX | Единая точка входа `:80`, лимит тела запроса, проксирование на API и MinIO |

### Размещение (docker compose, одна VM)

- `web` — FastAPI (API + страницы). Порт наружу не публикуется, только через NGINX.
- `worker` — arq-воркер, тот же образ, что `web`.
- `postgres` — БД; данные в томе.
- `redis` — очередь задач.
- `minio` — S3-хранилище; данные в томе.
- `nginx` — reverse proxy, единственный опубликованный порт `:80`.
- `seed` — one-shot контейнер демо-данных, запускается по требованию (`--profile seed`).

```mermaid
flowchart LR
    U[Браузер] -->|:80| N[NGINX]
    N -->|/api/*, страницы| W[FastAPI web]
    N -->|/media/*| M[MinIO]
    W --> PG[(PostgreSQL)]
    W --> R[(Redis)]
    W --> M
    K[arq worker] --> R
    K --> M
    K --> PG
```

## 2. Путь видео от загрузки до раздачи

### 2.1 Загрузка

1. Браузер отправляет `POST /api/videos` (multipart): файл + метаданные (название, описание, флаг приватности).
   Прогресс — через `XMLHttpRequest.upload.onprogress` на странице загрузки.
2. API проверяет: сессию, расширение + magic bytes (сигнатура контейнера), размер (`MAX_UPLOAD_SIZE`, по умолчанию 512 МБ), метаданные (pydantic).
3. API стримит файл в MinIO, бакет `raw` с ключом `<uuid>`; считает SHA-256 по ходу.
4. В БД создаётся запись `videos` со статусом `processing`; в очередь кладётся задача `transcode(video_id)`.

Обоснование: прямая multipart-загрузка через API проще presigned-upload и достаточна для одного файла ≤ 512 МБ; отдача_bytes не касается БД. Подробнее — ADR-003.

### 2.2 Фоновая обработка

1. Воркер забирает задачу `transcode(video_id)` из Redis (arq).
2. Скачивает исходник из `raw` в tmp, запускает ffmpeg:
   `ffmpeg -i in -vf scale=-2:480 -c:v libx264 -preset fast -c:a aac -movflags +faststart out.mp4`
3. Кладёт результат в бакет `videos` (`<uuid>.mp4`) и превью в `thumbnails` (`<uuid>.jpg`, кадр на 1-й секунде).
4. Обновляет `videos.status = ready` (+ длительность, размеры). При ошибке после ретраев — `failed`.

Статусная модель: `processing → ready | failed`, плюс `hidden` (модерация). В лентах и поиске видны только `ready` (+ приватные владельцу).

### 2.3 Раздача

- **Готовое видео**: API после проверки прав выдаёт браузеру presigned GET URL на `videos/<uuid>.mp4` (TTL 4 ч). Тег `<video src=...>` играет его; перемотка работает через Range-запросы, которые MinIO поддерживает (206 Partial Content).
- **Превью публичных видео**: отдаются через NGINX `/media/thumbnails/...` → MinIO без подписи (публичный bucket policy на чтение). Карточкам ленты подпись не нужна.
- **Превью приватных видео**: как и видео — presigned URL после проверки прав.
- Прямая ссылка на объект без подписи (для `videos`, `thumbnails` приватных, `raw`) → 403 от MinIO.

## 3. Приватные видео и защита от утечки

- Проверка прав выполняется в API до выдачи любого presigned URL: `videos.is_private = true` → доступен владельцу, белому списку (`video_acl`), модераторам и админам.
- Неавторизованный запрос страницы приватного видео → 404 (не 403, чтобы не раскрывать факт существования).
- Presigned URL короткоживущие (4 ч); выдача только через проверку прав → передача ссылки другу даёт доступ лишь на время TTL, без возможности обновления.
- `raw`-бакет полностью закрыт; исходники не раздаются никогда.
- Ссылка на страницу видео не содержит ключей хранилища; ключи объектов (uuid) не угадываемы.

Остаточный риск: обладатель presigned URL может расшарить его на время TTL. Принято для простоты; альтернатива (стрим-прокси через API с проверкой каждого Range) — ADR-004.

## 4. Модель ролей и прав

Роль в `users.role` (`user | moderator | admin`), проверяется на каждом запросе из БД (сессия не кэширует роль).

| Действие | guest | user | moderator | admin |
|---|---|---|---|---|
| Просмотр публичных видео | ✅ | ✅ | ✅ | ✅ |
| Просмотр приватных видео | ❌ | владелец / белый список | ✅ | ✅ |
| Загрузка | ❌ | ✅ | ✅ | ✅ |
| Лайки, комментарии | ❌ | ✅ | ✅ | ✅ |
| Жалоба на видео | ❌ | ✅ | ✅ | ✅ |
| Очередь модерации, скрытие/возврат видео | ❌ | ❌ | ✅ | ✅ |
| Назначение ролей, блокировка пользователей | ❌ | ❌ | ❌ | ✅ |

Реализация: FastAPI-зависимости `get_current_user` → `require_role("moderator")`; для ресурсов — проверка владения/доступа на уровне сервиса.

## 5. Фоновая обработка

- Очередь: **arq** на Redis. Задача: `transcode(video_id)`, `retries=2` c задержкой.
- Воркер — отдельный контейнер того же образа (entrypoint `arq`); ffmpeg внутри образа.
- Идемпотентность: задача повторно ставится только при загрузке; воркер перед стартом проверяет статус видео в БД.
- Наблюдаемость: логи воркера в stdout (docker logs); статус каждого видео виден в БД и в UI владельца (`processing/ready/failed`).

## 6. Развёртывание одной командой

`deploy.sh` на чистой Ubuntu:

1. Проверяет/ставит Docker + Docker Compose plugin (official apt-репозиторий).
2. Копирует `.env.example` → `.env` (если нет), генерирует недостающие секреты (`POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`, `SECRET_KEY`) и сохраняет в `.env`.
3. `docker compose build && docker compose up -d` (web, worker, postgres, redis, minio, nginx).
4. Ждёт здоровья postgres/minio, применяет миграции (контейнер миграций или `alembic upgrade head` при старте `web`).
5. Печатает URL приложения (`http://<host>/`) и напоминание про seed: `docker compose --profile seed run --rm seed`.

Всё состояние — в named volumes (`pgdata`, `minio_data`); повторный запуск идемпотентен.

## 7. Структура репозитория (план)

```text
.
├── AGENTS.md, CLAUDE.md, README.md
├── pyproject.toml, uv.lock, .env.example
├── deploy.sh                      # одна команда для чистой Ubuntu
├── docker-compose.yml
├── nginx/nginx.conf
├── docs/                          # TZ, requirements, architecture, db-schema, security, testing, plan, adr/
├── migrations/                    # alembic
├── src/app/
│   ├── main.py                    # FastAPI app, маршруты страниц
│   ├── api/                       # REST: auth, videos, comments, likes, channels, search, moderation, admin
│   ├── pages/                     # Jinja2-роуты экранов
│   ├── core/                      # config, security, deps (get_current_user, require_role), storage (MinIO client)
│   ├── services/                  # бизнес-логика: доступ к видео, лайки, модерация
│   ├── models.py                  # SQLAlchemy-модели
│   ├── schemas.py                 # pydantic-схемы
│   ├── templates/                 # base.html + экраны
│   └── static/                    # css, js
├── src/worker/                    # arq-воркер: transcode, thumbnail
├── scripts/seed.py                # демо-данные
└── tests/                         # unit (без Docker) + integration (маркер, с Docker)
```
