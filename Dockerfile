# Single image for both web (uvicorn) and worker (arq).
# Import root is /srv/app/src (PYTHONPATH, set below): `app.*` and `worker.*`.

FROM python:3.12-slim

# uv is installed via the official installer (no pip involved).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/app/src

WORKDIR /srv/app

# ffmpeg is required by the transcode worker.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (cached layer).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy the project code (invalidates only the last layers).
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts ./scripts

EXPOSE 8000

# Default command: web server. The worker overrides the command in compose.
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
