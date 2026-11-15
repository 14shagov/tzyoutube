# Схема PostgreSQL

> ORM: SQLAlchemy 2.0, миграции: Alembic.
> Медиа-файлы (исходники, транскоды, превью) — в S3-хранилище (Silo, ADR-001); в БД только ключи объектов.

## 1. ER-диаграмма

```mermaid
erDiagram
    users ||--o{ videos : "загружает"
    users ||--o{ comments : "пишет"
    users ||--o{ reactions : "оценивает"
    users ||--o{ subscriptions : "подписывается"
    users ||--o{ sessions : "входит"
    users ||--o{ video_reports : "жалуется"
    users ||--o{ video_acl : "получает доступ"
    users ||--o{ playlists : "владеет"
    users ||--o{ video_views : "смотрит"
    videos ||--o{ comments : "имеет"
    videos ||--o{ reactions : "имеет"
    videos ||--o{ video_reports : "получает"
    videos ||--o{ video_acl : "выдаёт доступ"
    videos ||--o{ playlist_items : "входит в"
    videos ||--o{ video_views : "имеет"
    playlists ||--o{ playlist_items : "содержит"
    comments ||--o{ comments : "ответы"

    users {
        uuid id PK
        varchar email UK
        varchar password_hash
        varchar channel_name UK
        text channel_description
        varchar role
        boolean is_blocked
        timestamptz created_at
    }
    sessions {
        uuid id PK
        uuid user_id FK
        timestamptz expires_at
        timestamptz created_at
    }
    videos {
        uuid id PK
        uuid user_id FK
        varchar title
        text description
        boolean is_private
        varchar status
        varchar raw_key
        varchar video_key
        varchar thumbnail_key
        varchar failure_reason
        int duration_seconds
        bigint size_bytes
        bigint view_count
        timestamptz created_at
    }
    video_views {
        uuid id PK
        uuid video_id FK
        uuid user_id FK "nullable для гостей"
        varchar viewer_fingerprint
        timestamptz created_at
    }
    reactions {
        uuid id PK
        uuid user_id FK
        uuid video_id FK
        smallint value "+1 или -1"
    }
    comments {
        uuid id PK
        uuid video_id FK
        uuid user_id FK
        uuid parent_id FK "nullable, один уровень"
        text text
        timestamptz created_at
    }
    subscriptions {
        uuid subscriber_id PK,FK
        uuid channel_id PK,FK
        timestamptz created_at
    }
    playlists {
        uuid id PK
        uuid user_id FK
        varchar title
        boolean is_private
        timestamptz created_at
    }
    playlist_items {
        uuid playlist_id PK,FK
        uuid video_id PK,FK
        int position
        timestamptz created_at
    }
    video_reports {
        uuid id PK
        uuid video_id FK
        uuid reporter_id FK
        varchar reason
        varchar comment "nullable"
        varchar status
        varchar resolution "nullable"
        varchar resolution_note "nullable"
        uuid resolved_by FK "nullable, SET NULL"
        timestamptz resolved_at "nullable"
        timestamptz created_at
    }
    video_acl {
        uuid video_id PK,FK
        uuid user_id PK,FK
        timestamptz created_at
    }
```

## 2. Таблицы

### users

| Поле | Тип | Ограничения |
|---|---|---|
| id | uuid | PK, default gen_random_uuid() |
| email | varchar(255) | UNIQUE, NOT NULL |
| password_hash | varchar(255) | NOT NULL, bcrypt |
| channel_name | varchar(100) | UNIQUE, NOT NULL |
| channel_description | text | NOT NULL, default '' (3A, миграция `e1f2a3b4c5d6`) |
| role | varchar(16) | NOT NULL, CHECK IN ('user','moderator','admin'), default 'user' |
| is_blocked | boolean | NOT NULL, default false |
| created_at | timestamptz | NOT NULL, default now() |

### sessions

| Поле | Тип | Ограничения |
|---|---|---|
| id | uuid | PK (значение cookie `session_id`) |
| user_id | uuid | FK → users(id) ON DELETE CASCADE |
| expires_at | timestamptz | NOT NULL |
| created_at | timestamptz | NOT NULL, default now() |

Индекс `(expires_at)` для чистки просроченных.

### videos

| Поле | Тип | Ограничения |
|---|---|---|
| id | uuid | PK, default gen_random_uuid() |
| user_id | uuid | FK → users(id) ON DELETE CASCADE |
| title | varchar(200) | NOT NULL |
| description | text | NOT NULL, default '' |
| is_private | boolean | NOT NULL, default false |
| status | varchar(16) | NOT NULL, CHECK IN ('processing','ready','failed','hidden','deleted') — `deleted` = мягкое удаление: невидим всем, объекты чистит воркер |
| raw_key | varchar(200) | NOT NULL — ключ исходника в хранилище |
| video_key | varchar(200) | NULL — ключ транскода; NOT NULL при status='ready' (проверка приложением) |
| thumbnail_key | varchar(200) | NULL — ключ превью |
| failure_reason | varchar(500) | NULL — причина падения транскода (C2, миграция `c8d9e0f1a2b3`) |
| duration_seconds | int | NOT NULL, default 0 |
| size_bytes | bigint | NOT NULL, default 0 |
| view_count | bigint | NOT NULL, default 0 |
| created_at | timestamptz | NOT NULL, default now() |

Индексы:

- `(status, created_at DESC)` — лента;
- `(user_id, created_at DESC)` — канал;
- частичный `(created_at DESC) WHERE status='ready' AND NOT is_private` — главная;
- GIN по `title` с `pg_trgm` — поиск ILIKE.

Мягкое удаление (FR-10): `status='deleted'` скрывает видео из всех лент и поиска, страница отдаёт 404; воркер задачей `cleanup_video` удаляет объекты `raw`/`videos`/`thumbnails` с ретраями и затем ставит финальную пометку. Запись сохраняется до успешной очистки хранилища.

### video_views

| Поле | Тип | Ограничения |
|---|---|---|
| id | uuid | PK |
| video_id | uuid | FK → videos(id) ON DELETE CASCADE |
| user_id | uuid | FK → users(id) ON DELETE CASCADE, NULL для гостей |
| viewer_fingerprint | varchar(64) | NOT NULL — user_id либо cookie-id гостя |
| created_at | timestamptz | NOT NULL, default now() |

Дедупликация «раз в 24 ч» — логикой приложения: ищем запись с тем же `(video_id, viewer_fingerprint)` за последние 24 ч; если нет — вставляем и инкрементируем `videos.view_count` в той же транзакции.

### reactions

| Поле | Тип | Ограничения |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | FK → users(id) ON DELETE CASCADE |
| video_id | uuid | FK → videos(id) ON DELETE CASCADE |
| value | smallint | NOT NULL, CHECK IN (1, -1) |
| created_at | timestamptz | NOT NULL, default now() |

UNIQUE(user_id, video_id): одна реакция на пользователя; повтор — переключение или снятие.

### comments

| Поле | Тип | Ограничения |
|---|---|---|
| id | uuid | PK |
| video_id | uuid | FK → videos(id) ON DELETE CASCADE |
| user_id | uuid | FK → users(id) ON DELETE CASCADE |
| parent_id | uuid | NULL, FK → comments(id) ON DELETE CASCADE; один уровень вложенности |
| text | text | NOT NULL, до 2000 символов |
| created_at | timestamptz | NOT NULL, default now() |

Индекс `(video_id, created_at)`.

### subscriptions

| Поле | Тип | Ограничения |
|---|---|---|
| subscriber_id | uuid | PK + FK → users(id) ON DELETE CASCADE — кто подписан |
| channel_id | uuid | PK + FK → users(id) ON DELETE CASCADE — на кого (канал = users.id) |
| created_at | timestamptz | NOT NULL, default now() |

Составной PK (subscriber_id, channel_id) = уникальность и идемпотентность подписки; индекс `(channel_id)` — счётчик подписчиков. Самоподписка запрещена на уровне сервиса (422), миграция `e1f2a3b4c5d6`.

### playlists

| Поле | Тип | Ограничения |
|---|---|---|
| id | uuid | PK |
| user_id | uuid | FK → users(id) ON DELETE CASCADE |
| title | varchar(200) | NOT NULL |
| is_private | boolean | NOT NULL, default false — приватный плейлист видит только владелец |
| created_at | timestamptz | NOT NULL, default now() |

Индекс `(user_id, created_at)`. Миграция `f1a2b3c4d5e6`.

### playlist_items

| Поле | Тип | Ограничения |
|---|---|---|
| playlist_id | uuid | PK + FK → playlists(id) ON DELETE CASCADE |
| video_id | uuid | PK + FK → videos(id) ON DELETE CASCADE |
| position | int | NOT NULL, default 0 |
| created_at | timestamptz | NOT NULL, default now() |

Составной PK = видео в плейлисте не дублируется (409). Индекс `(playlist_id, position)` — порядок выдачи. Позиция по умолчанию = max(position)+1 (приложение).

### video_reports

| Поле | Тип | Ограничения |
|---|---|---|
| id | uuid | PK |
| video_id | uuid | FK → videos(id) ON DELETE CASCADE |
| reporter_id | uuid | FK → users(id) ON DELETE CASCADE |
| reason | varchar(32) | NOT NULL, CHECK IN ('spam','scam','violence','hate','sexual','misleading','copyright','other') |
| comment | varchar(1000) | NULL |
| status | varchar(16) | NOT NULL, default 'new', CHECK IN ('new','resolved','dismissed') |
| resolution | varchar(16) | NULL, CHECK IN ('hide','unhide','dismiss') — журнал решения |
| resolution_note | varchar(500) | NULL — причина решения |
| resolved_by | uuid | NULL, FK → users(id) ON DELETE SET NULL — кто решил |
| resolved_at | timestamptz | NULL — когда |
| created_at | timestamptz | NOT NULL, default now() |

UNIQUE(video_id, reporter_id) — одна жалоба на видео от пользователя; индекс `(status, created_at)` — очередь модерации. Миграция `b1c2d3e4f5a6`.

### video_acl

| Поле | Тип | Ограничения |
|---|---|---|
| video_id | uuid | PK + FK → videos(id) ON DELETE CASCADE |
| user_id | uuid | PK + FK → users(id) ON DELETE CASCADE |
| created_at | timestamptz | NOT NULL, default now() |

PK (video_id, user_id) = уникальность пары (идемпотентное добавление).

## 3. Что где хранится

| Данные | Где | Почему |
|---|---|---|
| Метаданные, права, реакции, комментарии, сессии | PostgreSQL | Транзакционность, связи, индексы |
| Видеофайлы (raw, транскоды), превью | S3-хранилище (Silo) | Большие бинарные объекты, presigned URL, Range-запросы |
| Счётчик просмотров | videos.view_count (denormalized) | Быстрая выдача в ленте; обновляется в транзакции с video_views |
| Логи | stdout контейнеров | Стандартный Docker logging |

## 4. Миграции

- Alembic; сущности группируются по смыслу (auth, videos, social, moderation).
- Обязательный `downgrade()` в каждой миграции.
- `alembic upgrade head` выполняется при старте web-контейнера до запуска uvicorn — чистая Ubuntu получает схему автоматически.

## 5. Целостность

- Все FK — ON DELETE CASCADE, кроме `video_reports.resolved_by` (SET NULL) — история модерации сохраняется.
- Cookie `session_id` содержит UUID записи `sessions`; при логауте запись удаляется.
- `videos.video_key`/`thumbnail_key` заполняются воркером только после успешного транскода; статусная модель согласована с приложением.
