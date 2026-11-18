# Установка и запуск

Документ описывает, как поднять проект на чистой Ubuntu 22.04/24.04, как
запустить его вручную, как смотреть логи, обновлять и полностью очищать.

Короткий путь: `sudo ./deploy.sh` (см. [README](../README.md)). Ниже — то же
самое по шагам.

## 1. Локальный запуск (порядок действий)

1. Клонировать репозиторий и перейти в каталог:

   ```bash
   git clone https://github.com/14shagov/tzyoutube.git
   cd tzyoutube
   ```

2. Создать `.env` из примера и задать секреты (нужны, если не используется
   `deploy.sh`):

   ```bash
   cp .env.example .env
   openssl rand -hex 48   # → SECRET_KEY
   openssl rand -hex 24   # → POSTGRES_PASSWORD
   openssl rand -hex 16   # → S3_ACCESS_KEY
   openssl rand -hex 32   # → S3_SECRET_KEY
   chmod 600 .env
   ```

   Впишите сгенерированные значения в `.env` и не коммитьте этот файл —
   он уже в `.gitignore`.

3. Поднять стек:

   ```bash
   docker compose up -d --build
   ```

4. Дождаться готовности и проверить health-check:

   ```bash
   docker compose ps
   curl http://localhost/health      # {"status":"ok"}
   ```

   `web` при старте сам применяет миграции Alembic (`alembic upgrade head`).

5. Наполнить базу демо-данными (по желанию):

   ```bash
   docker compose --profile seed run --rm seed
   ```

   Скрипт создаёт пользователей, каналы, видео (ffmpeg генерирует клипы
   внутри контейнера), подписки, плейлисты, реакции, комментарии, просмотры
   и жалобы. Повторный запуск не создаёт дубликатов. В production требуется
   `DEMO_SEED=true`.

6. Открыть приложение: <http://localhost/> и <http://localhost/docs>.

## 2. `deploy.sh` — по шагам

Скрипт идемпотентен: его можно запускать повторно, данные и `.env` при этом
не теряются.

```bash
sudo ./deploy.sh              # с демо-данными
sudo ./deploy.sh --no-seed    # без демо-данных
```

Этапы выполнения:

1. **Проверка ОС** — читается `/etc/os-release`, требуется `ID=ubuntu`;
   на другой системе скрипт завершается с понятной ошибкой.
2. **Проверка прав** — скрипт должен быть запущен от root
   (`sudo ./deploy.sh`), иначе выводится подсказка.
3. **Установка Docker** — если Docker Engine или плагин `docker compose`
   отсутствуют, они ставятся из официального apt-репозитория
   `https://download.docker.com/linux/ubuntu` (GPG-ключ скачивается в файл,
   никогда не выполняется «curl | sh»). Затем проверяется, что демон
   Docker отвечает.
4. **Проверка порта 80** — порт должен быть свободен, иначе скрипт
   останавливается с указанием, что его занимает. Повторный запуск, когда
   уже работает наш nginx, ошибку не вызывает.
5. **Создание `.env`** — если файла нет, он создаётся из `.env.example`, а
   секреты (`SECRET_KEY`, `POSTGRES_PASSWORD`, `S3_ACCESS_KEY`,
   `S3_SECRET_KEY`) генерируются через `openssl rand`. Существующий `.env`
   не перезаписывается, секреты не перегенерируются (иначе данные в томе
   PostgreSQL стали бы недоступны).
6. **Первый администратор** — email и канал берутся из `ADMIN_EMAIL` и
   `ADMIN_CHANNEL_NAME`, пароль генерируется случайно и печатается один раз
   в конце установки. В логи он не попадает.
7. **Сборка и запуск** — `docker compose up -d --build`.
8. **Ожидание healthcheck** — скрипт ждёт, пока postgres, redis, silo, web,
   worker и nginx станут healthy (таймаут `HEALTH_TIMEOUT`, по умолчанию
   300 секунд).
9. **Проверка HTTP** — ожидается ответ от `http://localhost/health`
   (таймаут `HTTP_TIMEOUT`, по умолчанию 60 секунд).
10. **Демо-данные** — если не указан `--no-seed`, выполняется
    `docker compose --profile seed run --rm seed`.
11. **Итог** — печатаются адреса (`http://localhost/`,
    `http://localhost/docs`), демо-аккаунты и пароль первого администратора.

Полезные переменные окружения: `ADMIN_EMAIL`, `ADMIN_CHANNEL_NAME`,
`HEALTH_TIMEOUT`, `HTTP_TIMEOUT`.

## 3. Ручной запуск через Docker Compose

Если `deploy.sh` не подходит (например, ОС не Ubuntu), стек поднимается
вручную — теми же compose-файлами:

```bash
cp .env.example .env      # заполнить секреты (см. шаг 2 выше)
chmod 600 .env
docker compose up -d --build
docker compose ps
curl http://localhost/health
docker compose --profile seed run --rm seed    # опционально, демо-данные
```

Сервисы в compose: `postgres`, `redis`, `silo` (S3-хранилище), `web`
(FastAPI), `worker` (arq, транскод), `nginx` (единственный опубликованный
порт — локальный адрес `localhost:80`), `seed` (одноразовый, профиль
`seed`).

## 4. Логи

```bash
docker compose logs -f            # все сервисы, follow
docker compose logs -f web        # только веб-приложение
docker compose logs -f worker     # только воркер транскода
docker compose logs --tail 100 nginx
docker compose ps                 # статусы и healthcheck
```

Отдельные файлы логов Docker лежат в `/var/lib/docker/containers/`; обычный
пользователь их не читает, используйте `docker compose logs`.

## 5. Обновление

```bash
git pull
docker compose up -d --build
docker compose ps
curl http://localhost/health
```

Миграции применяются автоматически при старте контейнера `web`
(`alembic upgrade head`). Откат миграций в этой версии не предусмотрен —
делайте резервную копию данных перед обновлением схемы.

## 6. Полная очистка

Удалить контейнеры, сети и **тома** с данными (БД, Redis, хранилище):

```bash
docker compose down -v
docker compose --profile seed down -v   # если запускался профиль seed
```

После этого состояние полностью чистое: том с PostgreSQL удалён, поэтому
данных и демо-контента больше нет. Следующий запуск снова создаст `.env`
(если его удалить) и заполнит базу заново.

Если нужно удалить только тома, а контейнеры оставить:

```bash
docker volume ls
docker volume rm <имя_тома>
```

## 7. Типичные ошибки

### Порт 80 занят

Сообщение: `port 80 is already in use by another process.`

Занять порт может уже установленный nginx или apache в системе:

```bash
sudo ss -tlnp | grep ':80'
sudo systemctl stop nginx
sudo systemctl disable nginx      # чтобы не занимал порт после перезагрузки
sudo fuser -k 80/tcp              # если процесс не из systemd
```

После освобождения порта запустите `sudo ./deploy.sh` заново.

### Docker не установлен или не запущен

Сообщения: `this script requires Ubuntu`, `must run as root`,
`Docker daemon is not reachable`, `docker compose plugin still missing`.

```bash
docker --version          # Docker не установлен → поставьте вручную:
docker compose version    # плагин compose отсутствует → поставьте вручную:
sudo systemctl start docker
sudo systemctl status docker
```

Установка вручную (официальный apt-репозиторий, без «curl | sh»):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

### Другие частые сообщения

| Сообщение | Причина | Что делать |
|---|---|---|
| `timeout ... waiting for services to become healthy` | сервис не поднялся | `docker compose ps`, `docker compose logs --tail 50 <service>` |
| `http://localhost/health did not respond` | nginx или web не отвечают | `docker compose logs --tail 50 web` |
| `set .env already exists — left untouched` | `.env` уже был создан | это нормально: секреты не перегенерируются |
| `seed: refusing to seed in production without DEMO_SEED=true` | production без флага | добавьте `DEMO_SEED=true` (только для тестового стенда) |
| `permission denied` при записи `.env` | нет прав root | запускайте через `sudo` |
