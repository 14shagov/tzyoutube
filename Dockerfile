# Single image for both web (uvicorn) and worker (arq).
# Import root is /srv/app/src (PYTHONPATH, set below): `app.*` and `worker.*`.

FROM python:3.12-slim

# uv is installed via the official installer (no pip involved).
# Pinned version — never :latest (repo rule).
COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /uvx /bin/
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/app/src

WORKDIR /srv/app

# ffmpeg is required by the transcode worker.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# C4: dedicated non-root system user; the container never runs as root.
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /srv/app --shell /usr/sbin/nologin app

# Install dependencies first (cached layer; --no-dev keeps ruff/pytest
# out of the runtime image). --no-install-project: only third-party deps
# here; project code is used via PYTHONPATH, not installed.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev \
    # the venv and workdir were built as root — hand them to the app user
    && chown -R app:app /srv/app

# Copy the project code (invalidates only the last layers), owned by app.
COPY --chown=app:app src ./src
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app scripts ./scripts

USER app

EXPOSE 8000

# Default command: web server. --no-sync: the venv is fully built at
# image build time; runtime must never re-resolve or download packages.
# The worker overrides the command in compose (also with --no-sync).
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
