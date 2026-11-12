# Ручные проверки (интеграционные, требуют Docker)

Формат: команда → ожидание → что прислать обратно.
Все проверки выполняются из корня репозитория. Перед стартом:

```
Copy-Item .env.example .env   # если .env ещё нет
docker compose down -v        # чистый старт (удалит данные прошлых прогонов)
```

## 1. Сборка и запуск полного стека

- Команда: `docker compose up -d --build`
- Ожидание: все сервисы Up (healthy): postgres, redis, silo, web, worker,
  nginx; web применяет миграции при старте и отвечает.
- Прислать: вывод `docker compose ps` целиком.

## 2. Health-эндпоинт

- Команда: `curl http://localhost/health`
- Ожидание: `{"status":"ok"}` (HTTP 200) через nginx.
- Прислать: ответ целиком.

## 3. Миграции применились

- Команда: `docker compose exec postgres psql -U tzyoutube -d tzyoutube -c "\dt"`
- Ожидание: таблицы users, sessions, videos, video_views, reactions,
  comments, video_acl и alembic_version.
- Прислать: вывод команды.

## 4. Swagger доступен

- Команда: открыть `http://localhost/docs`
- Ожидание: страница Swagger UI со списком эндпоинтов (auth, videos, feed).
- Прислать: скриншот или список видимых путей.

## 5. Регистрация и вход (сквозной API-сценарий)

- Команды:
  ```
  curl -i -X POST http://localhost/auth/register -H "Content-Type: application/json" -d "{\"email\":\"user1@example.com\",\"password\":\"Password123\",\"channel_name\":\"Channel One\"}"
  curl -i -c cookies.txt -X POST http://localhost/auth/login -H "Content-Type: application/json" -d "{\"email\":\"user1@example.com\",\"password\":\"Password123\"}"
  curl -i -b cookies.txt http://localhost/auth/me
  ```
- Ожидание: 201 на регистрация; 200 + Set-Cookie session_id на входе;
  200 + email/channel_name на /me.
- Прислать: ответы всех трёх команд (заголовки с cookie можно замазать).

## 6. Загрузка видео и транскод (главный сценарий)

- Команды:
  ```
  curl -i -b cookies.txt -X POST http://localhost/api/videos -F "title=Demo" -F "file=@demo_media/sample.mp4"
  # подождать 5–10 секунд, затем:
  docker compose exec postgres psql -U tzyoutube -d tzyoutube -c "SELECT id,status,video_key,thumbnail_key,duration_seconds FROM videos;"
  ```
- Ожидание: 202 + id; статус меняется processing → ready; video_key =
  videos/<uuid>.mp4, thumbnail_key = thumbnails/<uuid>.jpg, duration > 0.
- Прислать: ответ загрузки + вывод SELECT.

## 7. Раздача видео (presigned) и превью

- Команды:
  ```
  curl -i -b cookies.txt http://localhost/api/videos/<id>
  # из ответа взять play_url, затем:
  curl -I "<play_url>"
  ```
- Ожидание: карточка содержит play_url (presigned, host localhost);
  HEAD по play_url → HTTP 200 или 206 от хранилища; без cookie/прав — 404.
- Прислать: JSON карточки и статус HEAD.

## 8. Лента, поиск, просмотры, реакции, комментарии

- Команды:
  ```
  curl -i http://localhost/api/feed
  curl -i "http://localhost/api/search?q=Demo"
  curl -i -X POST http://localhost/api/videos/<id>/view
  curl -i -b cookies.txt -X POST http://localhost/api/videos/<id>/reactions -H "Content-Type: application/json" -d "{\"value\":1}"
  curl -i -b cookies.txt -X POST http://localhost/api/videos/<id>/comments -H "Content-Type: application/json" -d "{\"text\":\"hello\"}"
  curl -i http://localhost/api/videos/<id>/comments
  ```
- Ожидание: лента содержит видео; поиск находит по «Demo»; view → 204;
  реакция → {"likes":1,"dislikes":0}; комментарий 201 + в списке.
- Прислать: краткие статусы/тела всех ответов.

## 9. Приватность (кратко, полная матрица — этап 3)

- Команды:
  ```
  docker compose exec postgres psql -U tzyoutube -d tzyoutube -c "UPDATE videos SET is_private=true WHERE title='Demo';"
  curl -i http://localhost/api/videos/<id>
  ```
- Ожидание: 404 для анонима (существование не раскрывается).
- Прислать: статус ответа.

## 10. Остановка

- Команда: `docker compose down`
- Ожидание: все контейнеры остановлены.
- Прислать: вывод (кратко).

## 11. Миграция применяется на пустой БД (регресс после m01611)

- Команды:
  ```
  docker compose down -v
  docker compose up -d --build
  # подождать ~20 секунд, затем:
  docker compose ps
  docker compose logs web --tail 50
  docker compose logs worker --tail 20
  docker compose exec postgres psql -U tzyoutube -d tzyoutube -c "\dt"
  ```
- Ожидание: web и worker Up (не Restarting); в логах web —
  `alembic upgrade head` без `ArgumentError`; в логах worker — строка
  вида `Starting worker: ... 1 functions` (транскод зарегистрирован);
  все таблицы (users, sessions, videos, video_views, reactions,
  comments, video_acl, alembic_version) созданы.
- Прислать: вывод `ps` + хвосты логов web и worker + вывод `\dt`.

## 12. Swagger за nginx: изменяющие запросы проходят (регресс после m01882)

- Команда: открыть `http://localhost/docs`, в Swagger UI выполнить
  `POST /auth/register` с телом
  `{"email":"swagger1@example.com","password":"Password123","channel_name":"Swagger One"}`.
- Ожидание: 201, тело с `channel_name` — НЕ 403 cross-origin.
- Прислать: скриншот ответа или код+тело.

## 13. CSRF: чужой Origin отклонён и виден в логах

- Команды:
  ```
  curl.exe -i -X POST http://localhost/auth/register -H "Origin: http://localhost" -H "Content-Type: application/json" --data-raw "{}"
  curl.exe -i -X POST http://localhost/auth/register -H "Origin: http://evil.example" -H "Content-Type: application/json" --data-raw "{}"
  docker compose logs web --tail 10
  ```
- Ожидание: первый запрос → 422 (валидация: пустое тело, но Origin
  принят); второй → 403 `{"detail":"cross-origin request rejected"}`;
  в логах web строка `WARNING ... csrf rejected: method=POST
  path=/auth/register origin='http://evil.example' ...` без cookie/секретов.
- Прислать: статусы обоих ответов + строку WARNING из логов.
