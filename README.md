# tzyoutube

Test project: a small YouTube clone (FastAPI + PostgreSQL + S3 storage +
arq worker + nginx, all in docker compose).

## Quick start (local)

```bash
cp .env.example .env        # fill in the placeholder secrets
docker compose up -d --build
```

Open http://localhost — API docs at http://localhost/docs.

## Deploying to a VM (HTTP)

The app rejects cross-origin browser requests (CSRF). It must know its
own public origin(s):

```bash
# in .env on the VM (IP deploy):
ALLOWED_ORIGINS=http://203.0.113.10
# or a domain:
ALLOWED_ORIGINS=https://video.example.com
```

Several addresses: separate with commas. After changing `.env`, restart:
`docker compose up -d`.

## Public S3 endpoint (presigned video links)

Presigned video URLs are signed for the PUBLIC address the browser
uses, not for the internal `silo:9000`. It is controlled by
`S3_PUBLIC_ENDPOINT` (default `localhost`, port 80):

```bash
# in .env on the VM — same host as ALLOWED_ORIGINS, without scheme:
S3_PUBLIC_ENDPOINT=203.0.113.10
# or a domain:
S3_PUBLIC_ENDPOINT=video.example.com
```

nginx (`/videos/`, `/thumbnails/`) forwards the browser's `Host` header
to Silo as-is, so the SigV4 signature stays valid. Signing itself is
offline — no network call to the storage.

`deploy.sh` (stage 5) will copy `.env.example` to `.env` and generate
secrets; set `ALLOWED_ORIGINS` and `S3_PUBLIC_ENDPOINT` in the
generated `.env` to your public address before (or after) the first
`docker compose up -d`.

## Cookie Secure flag

`COOKIE_SECURE` (default off) — turn it on only when serving over HTTPS,
otherwise browsers will refuse to store the session cookie on plain
HTTP and login will silently fail. With HTTPS: set `COOKIE_SECURE=true`
and `ALLOWED_ORIGINS=https://your-domain`.

## Tests

```bash
uv run pytest -q        # unit tests, no Docker
uv run ruff check .
```

Integration checks that need the real stack: docs/manual-checks.md.
