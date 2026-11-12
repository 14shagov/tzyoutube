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

`deploy.sh` (stage 5) will copy `.env.example` to `.env` and generate
secrets; set `ALLOWED_ORIGINS` in the generated `.env` to your public
address before (or after) the first `docker compose up -d`.

## Tests

```bash
uv run pytest -q        # unit tests, no Docker
uv run ruff check .
```

Integration checks that need the real stack: docs/manual-checks.md.
