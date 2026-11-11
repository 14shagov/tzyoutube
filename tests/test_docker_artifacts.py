"""Static tests for Docker artifacts (no Docker needed to run these).

They parse the YAML/Dockerfile and assert repo rules:
- only versioned image tags (`:latest` is forbidden anywhere);
- no references to removed MinIO images (minio/minio, minio/mc, quay.io/minio);
- all runtime service commands use the same import root (`app.*` / `worker.*`,
  never `src.*` which only exists in the host layout);
- runtime commands never trigger a uv re-sync (`uv run` must carry --no-sync);
- the runtime image installs only production dependencies (--no-dev),
  and dependencies are installed before the code is copied (cache-friendly).
"""

import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = PROJECT_ROOT / "docker-compose.yml"
DOCKERFILE_PATH = PROJECT_ROOT / "Dockerfile"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _dockerfile() -> str:
    return DOCKERFILE_PATH.read_text(encoding="utf-8")


def test_compose_declares_expected_services() -> None:
    compose = _compose()
    services = compose["services"]
    for name in ("postgres", "redis", "silo", "web", "worker", "nginx"):
        assert name in services, f"service {name} missing in docker-compose.yml"


def test_compose_publishes_only_nginx() -> None:
    compose = _compose()
    for name, svc in compose["services"].items():
        ports = svc.get("ports")
        if ports:
            for published in ports:
                # published entries like "80:80" — only nginx may expose ports
                assert name == "nginx", f"{name} publishes ports: {ports}"


def test_no_latest_tags_anywhere() -> None:
    offenders: list[str] = []
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    for i, line in enumerate(compose_text.splitlines(), 1):
        if re.search(r"image:\s*\S+:latest", line):
            offenders.append(f"docker-compose.yml:{i}")
    if re.search(r"--from=\S+:latest", _dockerfile()):
        offenders.append("Dockerfile (COPY --from)")
    assert not offenders, f":latest tags are forbidden, found at {offenders}"


def test_no_removed_minio_images() -> None:
    """MinIO images were removed from Docker Hub; Silo (ADR-001) is used."""
    forbidden = re.compile(r"minio/minio|minio/mc|quay\.io/minio")
    for path in (COMPOSE_PATH, DOCKERFILE_PATH, PROJECT_ROOT / "nginx" / "nginx.conf"):
        text = path.read_text(encoding="utf-8")
        matches = forbidden.findall(text)
        assert not matches, f"{path.name} references removed MinIO images: {matches}"


def test_service_commands_use_unified_import_root() -> None:
    """Runtime commands must use the in-image import root (PYTHONPATH=/srv/app/src):
    `app.*` for web/alembic/seed, `worker.*` for arq — never `src.*`."""
    compose = _compose()
    for name, svc in compose["services"].items():
        command = svc.get("command", "")
        if isinstance(command, list):
            command = " ".join(command)
        assert "src." not in command, (
            f"service {name} imports from `src.` — the image uses PYTHONPATH=/srv/app/src"
        )


def test_runtime_commands_never_resync() -> None:
    """Every `uv run` in compose must pass --no-sync: the venv is built at
    image build time and must not be re-resolved at container start."""
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    for i, line in enumerate(compose_text.splitlines(), 1):
        if "uv run" in line and "--no-sync" not in line:
            raise AssertionError(f"docker-compose.yml:{i} `uv run` without --no-sync: {line.strip()}")


def test_dockerfile_installs_only_prod_deps() -> None:
    text = _dockerfile()
    assert "uv sync --frozen --no-install-project --no-dev" in text, (
        "runtime image must install only production dependencies (--no-dev)"
    )


def test_dockerfile_deps_layer_before_code() -> None:
    """Dependencies must be copied/installed before the source code so that
    code changes do not invalidate the dependency cache layer."""
    text = _dockerfile()
    sync_pos = text.index("uv sync --frozen")
    code_pos = text.index("COPY src ./src")
    assert sync_pos < code_pos, "deps layer must come before the code COPY layers"


def test_dockerfile_uses_uv() -> None:
    text = _dockerfile()
    assert "uv" in text
    # uv installed via the official installer, not pip
    assert not re.search(r"^\s*pip\s+install", text, re.MULTILINE)


def test_web_runs_migrations_on_start() -> None:
    compose = _compose()
    web_cmd = compose["services"]["web"].get("command", "")
    assert "alembic upgrade head" in web_cmd
