#!/usr/bin/env bash
# deploy.sh — one-command local deploy on Ubuntu 22.04/24.04.
#
# Idempotent: safe to re-run. Brings up the whole stack (app, DB, S3 storage,
# background worker, nginx) bound to loopback only (http://localhost), seeds
# demo data, and prints the addresses + demo accounts at the end.
#
# Usage: sudo ./deploy.sh [--no-seed]
#
# Docker Engine (if missing) is installed from the OFFICIAL apt repository:
#   https://download.docker.com/linux/ubuntu
# (see docs/setup.md). Nothing is ever piped from curl/wget into a shell.
set -euo pipefail

HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-300}"   # seconds to wait for service health
HTTP_TIMEOUT="${HTTP_TIMEOUT:-60}"        # seconds to wait for /health via nginx
SEED=1
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"
ADMIN_CHANNEL="${ADMIN_CHANNEL_NAME:-AdminChannel}"

for arg in "$@"; do
    case "$arg" in
        --no-seed) SEED=0 ;;
        -h|--help)
            echo "Usage: sudo ./deploy.sh [--no-seed]"
            echo "  ADMIN_EMAIL / ADMIN_CHANNEL_NAME env vars configure the first admin."
            exit 0
            ;;
        *)
            echo "[deploy] ERROR: unknown option: $arg (supported: --no-seed)" >&2
            exit 2
            ;;
    esac
done

log() { printf '[deploy] %s\n' "$*"; }
err() { printf '[deploy] ERROR: %s\n' "$*" >&2; }

# --- 1. OS check: Ubuntu only ------------------------------------------------
if [ ! -f /etc/os-release ]; then
    err "cannot determine OS: /etc/os-release is missing"
    exit 1
fi
# shellcheck disable=SC1091
. /etc/os-release
if [ "${ID:-}" != "ubuntu" ]; then
    err "this script requires Ubuntu (detected: ${PRETTY_NAME:-unknown})."
    err "On other distros install Docker manually and run: docker compose up -d --build"
    exit 1
fi
log "OS: ${PRETTY_NAME}"

# --- 2. Root check ------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    err "must run as root: sudo ./deploy.sh"
    exit 1
fi

# --- 3. Docker Engine + compose plugin ----------------------------------------
install_docker() {
    log "Installing Docker Engine from the official apt repo (download.docker.com)..."
    apt-get update
    apt-get install -y ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    # GPG key fetched to a FILE (never piped into a shell).
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
}

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    install_docker
fi
docker compose version >/dev/null 2>&1 || { err "docker compose plugin still missing after install"; exit 1; }
if ! docker info >/dev/null 2>&1; then
    err "Docker daemon is not reachable. Try: sudo systemctl start docker"
    exit 1
fi
log "Docker: $(docker --version), compose: $(docker compose version --short 2>/dev/null || echo ok)"

# --- 4. Port 80 must be free (unless our own nginx already runs) --------------
port80_busy() {
    (exec 3<>/dev/tcp/127.0.0.1/80) 2>/dev/null && return 0
    ss -tln 2>/dev/null | grep -qE '0\.0\.0\.0:80|\[::\]:80|127\.0\.0\.1:80' && return 0
    return 1
}
if ! docker compose ps --services --filter status=running 2>/dev/null | grep -qx nginx; then
    if port80_busy; then
        err "port 80 is already in use by another process."
        err "Free it (e.g. 'sudo systemctl stop nginx' or 'sudo fuser -k 80/tcp') and re-run."
        exit 1
    fi
fi

# --- 5. .env: create from .env.example with random secrets ONLY if missing ----
# An existing .env is NEVER overwritten and its secrets are NEVER regenerated
# (otherwise the Postgres data volume would be locked out by a new password).
if [ ! -f .env ]; then
    log "Creating .env from .env.example with random secrets (openssl rand)..."
    cp .env.example .env
    _set_env() {
        local key="$1" value="$2" escaped
        escaped=$(printf '%s' "$value" | sed 's/[&|]/\\&/g')
        sed -i "s|^${key}=.*|${key}=${escaped}|" .env
    }
    _set_env SECRET_KEY "$(openssl rand -hex 48)"
    _set_env POSTGRES_PASSWORD "$(openssl rand -hex 24)"
    _set_env S3_ACCESS_KEY "$(openssl rand -hex 16)"
    _set_env S3_SECRET_KEY "$(openssl rand -hex 32)"
    chmod 600 .env
    log ".env created. Secrets live only in .env — they are never printed or logged."
else
    log ".env already exists — left untouched (secrets not regenerated)."
fi

# --- 6. First admin: email/channel from env, password generated once ----------
ADMIN_PASSWORD="$(openssl rand -base64 24 | tr -d '\n')"

# --- 7. Build and start the stack ---------------------------------------------
log "Running: docker compose up -d --build"
docker compose up -d --build

# --- 8. Wait for every service to be healthy ----------------------------------
wait_for_healthy() {
    local timeout="${1:-300}"
    local deadline=$((SECONDS + timeout))
    local services="postgres redis silo web worker nginx"
    log "Waiting up to ${timeout}s for all services to become healthy..."
    while (( SECONDS < deadline )); do
        local ok=1
        for svc in $services; do
            local cid health state
            cid=$(docker compose ps -q "$svc" 2>/dev/null || true)
            if [ -z "$cid" ]; then ok=0; continue; fi
            health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid" 2>/dev/null || echo "")
            state=$(docker inspect --format '{{.State.Status}}' "$cid" 2>/dev/null || echo "")
            if [ "$state" != "running" ] || { [ -n "$health" ] && [ "$health" != "healthy" ]; }; then
                ok=0
            fi
        done
        if (( ok )); then
            log "All services are healthy."
            return 0
        fi
        sleep 5
    done
    err "timed out after ${timeout}s waiting for services to become healthy."
    docker compose ps || true
    return 1
}
wait_for_healthy "$HEALTH_TIMEOUT" || exit 1

# --- 9. Wait for /health through nginx ----------------------------------------
wait_for_http() {
    local url="$1" timeout="${2:-60}"
    local deadline=$((SECONDS + timeout))
    while (( SECONDS < deadline )); do
        if curl -fsS -o /dev/null "$url" 2>/dev/null; then
            return 0
        fi
        sleep 3
    done
    return 1
}
if ! wait_for_http "http://localhost/health" "$HTTP_TIMEOUT"; then
    err "http://localhost/health did not respond within ${HTTP_TIMEOUT}s."
    docker compose logs --tail 50 web || true
    exit 1
fi
log "http://localhost/health is OK."

# --- 10. Seed demo data (unless --no-seed) -------------------------------------
if [ "$SEED" -eq 1 ]; then
    log "Seeding demo data (skip with --no-seed)..."
    DEMO_SEED=true \
    ADMIN_EMAIL="$ADMIN_EMAIL" \
    ADMIN_CHANNEL_NAME="$ADMIN_CHANNEL" \
    ADMIN_PASSWORD="$ADMIN_PASSWORD" \
        docker compose --profile seed run --rm seed
    log "Seed complete."
else
    log "Skipping seed (--no-seed)."
fi

# --- 11. Summary ---------------------------------------------------------------
log "=============================================================="
log "Deployed successfully. Open:"
log "  http://localhost/       — web UI"
log "  http://localhost/docs   — API docs (Swagger)"
log ""
log "Demo accounts (DEMO ONLY — fixed passwords, see README):"
log "  mod@example.com    /  DemoMod123     (role: moderator)"
log "  user1@example.com  /  DemoUser123    (role: user)"
log "  user2@example.com  /  DemoUser123"
log "  user3@example.com  /  DemoUser123"
log ""
log "First admin (email/channel from environment variables):"
log "  email:    ${ADMIN_EMAIL}"
log "  channel:  ${ADMIN_CHANNEL}"
printf '  password: %s\n' "$ADMIN_PASSWORD"
log "  (password printed once here only — never written to any log)"
log "=============================================================="
