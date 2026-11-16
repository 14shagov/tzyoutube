# ADR-005: Структура проекта — модульный монолит

Статус: принято.

## Контекст

Срок 10 дней, один разработчик + ревьюер. Стек зафиксирован: FastAPI + воркер транскодирования + PostgreSQL + S3-хранилище (Silo, форк MinIO — ADR-001) + Redis + NGINX.

## Варианты

1. **Микросервисы (отдельные репо/сервисы на домен).** Развёртывание и кодовая база дороже на порядок — избыточно.
2. **Монорепо с пакетами per-service.** Гибко, но настройка сборки/импортов съест время.
3. **Модульный монолит: один uv-проект, `src/app` (web) + `src/worker`, общие модели/сервисы.**

## Решение

Вариант 3. Один образ Docker; web и worker — один и тот же образ с разной командой (`uvicorn` / `arq`). Раскладка:

```
src/app/           # FastAPI: api/, pages/, services/, core/, models.py, schemas.py, templates/, static/
src/worker/        # arq воркер: settings, jobs (transcode)
migrations/        # Alembic
scripts/           # seed.py
tests/             # unit/ + integration/
nginx/             # default.conf.template (envsubst, ADR-008)
docker-compose.yml # web, worker, db, redis, silo, nginx (+ профиль seed)
deploy.sh
```

Границы: `services/` не знают про HTTP; `api/` и `pages/` тонкие; storage и БД — за интерфейсами (`core/storage.py`), чтобы юнит-тесты работали на фейках.

## Последствия

- Плюс: минимум сборки, общие типы, простые импорты, быстрая навигация.
- Минус: образ воркера несёт и web-зависимости — приемлемо (один образ, разные команды).
