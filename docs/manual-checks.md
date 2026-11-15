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
  comments, video_acl, subscriptions, playlists, playlist_items,
  video_reports и alembic_version.
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
  # регресс presign-offline (баг реального стека): карточка должна отдаваться
  # БЕЗ 500 и без обращений web-контейнера к публичному хосту —
  docker compose logs web --tail 30 | Select-String -Pattern "location|MaxRetry|Connection refused"
  ```
- Ожидание: карточка 200, содержит play_url (presigned, host = S3_PUBLIC_ENDPOINT,
  напр. `http://localhost/videos/<uuid>.mp4?X-Amz-...`); в логах web НЕТ
  MaxRetryError/`?location=` (presign теперь локальный, region задан явно);
  HEAD по play_url → HTTP 200 или 206 от хранилища; без cookie/прав — 404.
- Прислать: JSON карточки, статус HEAD, и факт отсутствия ошибок в логах web.

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

## 14. Загрузка mp4 через Swagger → processing → ready → ссылка открывается (регресс после m02086)

- Команды:
  ```
  # 1) залогиниться (или зарегистрироваться — сессия откроется сразу):
  curl.exe -i -c cookies.txt -X POST http://localhost/auth/login -H "Origin: http://localhost" -H "Content-Type: application/json" --data-raw "{\"email\":\"user1@example.com\",\"password\":\"Password123\"}"
  # 2) загрузить ролик (магические байты mp4):
  curl.exe -i -b cookies.txt -X POST http://localhost/api/videos -H "Origin: http://localhost" -F "file=@demo_media/sample.mp4" -F "title=Manual check"
  # записать id из ответа (202), затем:
  docker compose exec postgres psql -U tzyoutube -d tzyoutube -c "SELECT id,status,video_key,thumbnail_key FROM videos ORDER BY created_at DESC LIMIT 1;"
  # подождать ~10 секунд (воркер транскодит), повторить SELECT
  # 3) получить карточку:
  curl.exe -i -b cookies.txt http://localhost/api/videos/<id> -H "Origin: http://localhost"
  ```
  Затем открыть ссылку `play_url` из карточки в браузере.
- Ожидание: 202 на загрузке; первый SELECT — `processing`; второй —
  `ready` c заполненными `video_key`/`thumbnail_key`; карточка содержит
  `play_url` вида `http://localhost/videos/<uuid>.mp4?...`; ссылка
  открывается в браузере и видео воспроизводится (или скачивается).
- Прислать: ответы обоих SELECT, `play_url`, статус открытия ссылки.

## 15. Presign офлайн, region явный (регресс реального стека: GET /api/videos/{id} → 500 MaxRetryError ?location=)

- Команды:
  ```
  docker compose exec web sh -c 'env | Select-String -Pattern "S3_REGION"'   # PowerShell: docker compose exec web env | findstr S3_REGION
  curl.exe -i http://localhost/api/feed
  curl.exe -i "http://localhost/api/search?q=Manual"
  curl.exe -i http://localhost/api/videos/<id>     # ready-видео из п.14
  docker compose logs web --tail 40
  ```
- Ожидание: в env web есть `S3_REGION=us-east-1` (и у silo `MINIO_REGION` тем же);
  feed/search/карточка → 200; у карточки `play_url` с host публичного endpoint
  и `X-Amz-Signature`; в логах web нет `MaxRetryError`/`?location=`/`Connection refused`.
- Прислать: вывод env, статусы трёх GET, `play_url`, хвост логов web.

## 16. Playback в браузере: видео проигрывается, а не скачивается (регресс content-type)

- Предусловие: видео из п.14 в статусе ready.
- Команды (PowerShell, range-запрос как у браузерного <video>):
  ```
  curl.exe -s -D - -o NUL -r 0-0 "<play_url>"
  ```
- Ожидание: `HTTP/1.1 206 Partial Content`, `Content-Type: video/mp4`,
  `Content-Disposition: inline`, `Accept-Ranges: bytes`, ровно один
  `X-Content-Type-Options: nosniff` (не дублируется от silo + nginx).
  Presigned URL содержит `response-content-type=video%2Fmp4` и
  `response-content-disposition=inline` (покрывает и старые объекты).
- Также: открыть `play_url` в браузере — должен играть inline, не скачиваться;
  превью público: `curl.exe -s -D - -o NUL http://localhost/media/thumbnails/<uuid>.jpg`
  → `Content-Type: image/jpeg`, `Cache-Control: public, max-age=3600`, один nosniff.
- Прислать: заголовки 206-ответа, заголовки превью, скрин/описание поведения браузера.

## 17. Этап 3–4 API: каналы, подписки, управление видео, плейлисты, модерация (группы 3A–3F)

- Предусловие: два пользователя user1/user2 (п.5), видео Demo в ready (п.14).
- Команды (PowerShell; `-b cookies.txt` = user1, `-c c2.txt` + login user2 = второй):
  ```
  # 3A канал: карточка, видео канала, PATCH своего
  curl.exe -i http://localhost/api/channels/<user1_id>
  curl.exe -i http://localhost/api/channels/<user1_id>/videos
  curl.exe -i -b cookies.txt -X PATCH http://localhost/api/channels/<user1_id> -H "Origin: http://localhost" -H "Content-Type: application/json" --data-raw '{"description":"мой канал"}'
  # 3B подписки: подписка/отписка идемпотентны, self → 422, ленты подписок
  curl.exe -i -b c2.txt -X POST http://localhost/api/channels/<user1_id>/subscribe -H "Origin: http://localhost"
  curl.exe -i -b c2.txt -X POST http://localhost/api/channels/<user1_id>/subscribe -H "Origin: http://localhost"   # повтор → 204
  curl.exe -i -b c2.txt -X POST http://localhost/api/channels/<user2_id>/subscribe -H "Origin: http://localhost"   # self → 422
  curl.exe -i -b c2.txt http://localhost/api/subscriptions
  curl.exe -i -b c2.txt http://localhost/api/feed/subscriptions
  # 3C управление: PATCH/DELETE, /me/videos, ACL по email, статус
  curl.exe -i -b cookies.txt -X PATCH http://localhost/api/videos/<id> -H "Origin: http://localhost" -H "Content-Type: application/json" --data-raw '{"is_private":true,"title":"Demo private"}'
  curl.exe -i -b cookies.txt http://localhost/api/me/videos
  curl.exe -i -b cookies.txt -X POST http://localhost/api/videos/<id>/acl -H "Origin: http://localhost" -H "Content-Type: application/json" --data-raw '{"email":"user2@example.com"}'
  curl.exe -i http://localhost/api/videos/<id>                                   # гость → 404 (приватное)
  curl.exe -i -b c2.txt http://localhost/api/videos/<id>                         # ACL → 200 с play_url (TTL 900)
  curl.exe -i -b cookies.txt http://localhost/api/videos/<id>/status
  curl.exe -i -b cookies.txt -X DELETE http://localhost/api/videos/<id>/acl/<user2_id> -H "Origin: http://localhost"
  # мягкое удаление: статус deleted, из ленты/поиска исчез, воркер чистит объекты
  curl.exe -i -b cookies.txt -X DELETE http://localhost/api/videos/<id> -H "Origin: http://localhost"   # → 204
  curl.exe -i http://localhost/api/feed                                            # видео нет
  curl.exe -i -b cookies.txt http://localhost/api/videos/<id>                     # → 404
  docker compose logs worker --tail 20                                            # delete_video: объекты удалены
  # 3D плейлисты
  curl.exe -i -b cookies.txt -X POST http://localhost/api/playlists -H "Origin: http://localhost" -H "Content-Type: application/json" --data-raw '{"title":"Избранное"}'
  curl.exe -i -b cookies.txt -X POST http://localhost/api/playlists/<pl>/items -H "Origin: http://localhost" -H "Content-Type: application/json" --data-raw '{"video_id":"<id2>"}'
  curl.exe -i http://localhost/api/playlists/<pl>
  curl.exe -i http://localhost/api/channels/<user1_id>/playlists
  # 3E модерация: жалоба, очередь, решение, админ
  curl.exe -i -b c2.txt -X POST http://localhost/api/videos/<id2>/report -H "Origin: http://localhost" -H "Content-Type: application/json" --data-raw '{"reason":"spam","comment":"test"}'
  curl.exe -i -b c2.txt -X POST http://localhost/api/videos/<id2>/report -H "Origin: http://localhost" -H "Content-Type: application/json" --data-raw '{"reason":"spam"}'   # повтор → 409
  curl.exe -i -b c2.txt http://localhost/api/mod/reports                          # user → 403; нужен moderator (seed)
  curl.exe -i -b mod.txt -X POST http://localhost/api/mod/reports/<rid>/resolve -H "Origin: http://localhost" -H "Content-Type: application/json" --data-raw '{"action":"hide","note":"spam"}'
  curl.exe -i http://localhost/api/videos/<id2>                                   # скрытое: гость → 404
  curl.exe -i -b cookies.txt http://localhost/api/videos/<id2>                    # владелец → 200
  curl.exe -i -b admin.txt -X PATCH http://localhost/api/admin/users/<user2_id> -H "Origin: http://localhost" -H "Content-Type: application/json" --data-raw '{"is_blocked":true}'
  curl.exe -i -X POST http://localhost/auth/login -H "Content-Type: application/json" --data-raw '{"email":"user2@example.com","password":"Password123"}'   # → 401
  ```
- Ожидание: статусы как в комментариях (200/201/204/401/403/404/409/422);
  после DELETE видео — запись `status='deleted'`, объекты в silo удалены
  (воркер `delete_video` в логах), из ленты/поиска/канала видео нет;
  скрытое видео видно только владельцу/модератору; заблокированный user2
  получает 401 на login и на /auth/me со старой сессией.
- Прислать: ключевые статусы/тела + хвост логов worker.
