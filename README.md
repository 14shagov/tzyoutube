git clone https://github.com/14shagov/tzyoutube.git && cd tzyoutube && sudo ./deploy.sh

# tzyoutube

Локальный видеохостинг: загрузка видео, фоновый транскод, лента, поиск,
подписки, плейлисты, комментарии и модерация. Бэкенд — FastAPI (REST API +
SSR на Jinja2), PostgreSQL 16, S3-совместимое хранилище Silo, Redis + arq для
транскода, NGINX как единственная опубликованная точка входа. Весь стек
поднимается одной командой на чистой Ubuntu — без домена и без публикации в
интернет (порт 80 слушает только `127.0.0.1`).

## Быстрый старт

Одна команда (см. первую строку файла):

```bash
sudo ./deploy.sh
```

Скрипт: проверяет Ubuntu и права root, при необходимости ставит Docker Engine
и плагин docker compose из официального apt-репозитория
(`https://download.docker.com/linux/ubuntu`), создаёт `.env` из `.env.example`
с секретами, сгенерированными через `openssl rand` (существующий `.env` не
перезаписывается и секреты не перегенерируются), поднимает стек
(`docker compose up -d --build`), дожидается healthcheck всех сервисов и
ответа `http://localhost/health`, затем наполняет базу демо-данными и
печатает адреса, демо-аккаунты и пароль первого администратора (один раз).

Полезные флаги и переменные:

- `sudo ./deploy.sh --no-seed` — без демо-данных.
- `ADMIN_EMAIL`, `ADMIN_CHANNEL_NAME` — первый администратор (по умолчанию
  `admin@example.com` / `AdminChannel`).
- `HEALTH_TIMEOUT`, `HTTP_TIMEOUT` — таймауты ожидания (секунды).

## Требования к машине

| Требование | Значение |
|---|---|
| ОС | Ubuntu 22.04 или 24.04 (x86_64) |
| Права | root или `sudo` (нужны для Docker и `.env` с правами 600) |
| Docker | Engine + плагин Compose; ставится скриптом, если их нет |
| Порт 80 | свободен на `127.0.0.1` (единственный публикуемый порт) |
| Ресурсы | ~2 ГБ RAM, ~5 ГБ диска (образы, БД, видео) |
| Сеть | доступ к `download.docker.com` и `docker.io` при первом запуске |

Дополнительное ПО ставит сам `deploy.sh`: Docker Engine, Docker Compose
plugin, ffmpeg (нужен воркеру для транскода).

## Адреса

| Адрес | Что |
|---|---|
| http://localhost/ | веб-интерфейс (лента, поиск, каналы, плейлисты, загрузка) |
| http://localhost/docs | Swagger UI (REST API) |
| http://localhost/health | health-check |

Все адреса локальные: NGINX публикует `127.0.0.1:80:80`, наружу ничего не
открыто. Свои `ALLOWED_ORIGINS` / `S3_PUBLIC_ENDPOINT` задаются в `.env`.

## Демо-аккаунты

Фиксированные пароли, **только для демо** — на реальных инсталляциях так
оставлять нельзя:

| Логин | Пароль | Роль |
|---|---|---|
| admin@example.com | DemoAdmin123 | администратор (см. ниже) |
| mod@example.com | DemoMod123 | модератор |
| user1@example.com | DemoUser123 | пользователь |
| user2@example.com | DemoUser123 | пользователь |
| user3@example.com | DemoUser123 | пользователь |

Пароль **первого** администратора (`ADMIN_EMAIL`, по умолчанию
`admin@example.com`) `deploy.sh` генерирует случайно и печатает один раз в
конце установки; в логи он не попадает. Остальные демо-аккаунты создаются
сидом с паролями из таблицы.

## Архитектура

| Компонент | Технология | Роль |
|---|---|---|
| API + SSR | FastAPI, Jinja2 | REST API, страницы, авторизация, выдача presigned-ссылок |
| БД | PostgreSQL 16 | пользователи, видео, комментарии, реакции, подписки, плейлисты, жалобы |
| Хранилище | Silo (S3-совместимый) | исходники, готовые видео, превью |
| Фоновые задачи | Redis 7 + arq | транскод ffmpeg, генерация превью, удаление файлов |
| Reverse proxy | NGINX | единственная точка входа `:80`, лимиты запросов, раздача видео |

Документация:

- [docs/architecture.md](docs/architecture.md) — компоненты и потоки данных.
- [docs/requirements.md](docs/requirements.md) — требования, критерии приёмки, допущения.
- [docs/db-schema.md](docs/db-schema.md) — схема PostgreSQL.
- [docs/security.md](docs/security.md) — модель угроз и меры защиты.
- [docs/status.md](docs/status.md) — журнал статуса: сделано / дальше / проблемы.
- [docs/manual-checks.md](docs/manual-checks.md) — ручные интеграционные проверки.
- [docs/adr/](docs/adr/) — ADR-001…ADR-009: обоснования архитектурных решений.
- [docs/testing.md](docs/testing.md) — стратегия тестирования.

## Тесты

```bash
uv run pytest -q        # юнит-тесты, Docker не нужен
uv run ruff check .     # линтер
```

Логика сида покрыта отдельным набором (`tests/test_seed_unit.py`): гейт
`DEMO_SEED`, генерация ffmpeg-клипов без внешних файлов, полный набор
демо-данных и идемпотентность повторного запуска.

## Структура репозитория

```
src/app/          # FastAPI: api/ (REST), pages/ (SSR), services/, core/, models.py, templates/, static/
src/worker/       # arq-воркер транскода
migrations/       # миграции Alembic
scripts/seed.py   # наполнение демо-данными (логика — src/app/seed.py)
tests/            # юнит-тесты (+ integration с маркером integration)
nginx/            # конфигурация NGINX
docs/             # проектная документация
docker-compose.yml  # весь стек (postgres, redis, silo, web, worker, nginx, seed)
deploy.sh         # развёртывание одной командой на чистой Ubuntu
```
