"""Static tests for Docker artifacts (no Docker needed to run these).

They parse the YAML/Dockerfile and assert repo rules:
- only versioned image tags (`:latest` is forbidden anywhere);
- no references to removed MinIO images (minio/minio, minio/mc, quay.io/minio);
- all runtime service commands use the same import root (`app.*` / `worker.*`,
  never `src.*` which only exists in the host layout);
- runtime commands never trigger a uv re-sync (`uv run` must carry --no-sync);
- the runtime image installs only production dependencies (--no-dev),
  and dependencies are installed before the code is copied (cache-friendly);
- every COPY/ADD source in the Dockerfile exists in the repo and is
  tracked by git (so a missing dir breaks tests, not the CI build).
"""

import re
import subprocess
from pathlib import Path

import pytest
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
    m = re.search(r"^COPY\s+(--\S+\s+)*src\s+\./src\s*$", text, re.MULTILINE)
    assert m, "code COPY of ./src not found"
    assert sync_pos < m.start(), "deps layer must come before the code COPY layers"


def test_dockerfile_runs_as_non_root() -> None:
    """C4: the container must not run as root — dedicated system user,
    ownership fix after uv sync, USER app before EXPOSE/CMD."""
    text = _dockerfile()
    assert re.search(r"groupadd\s+--system\s+\S+", text), "no system groupadd"
    assert re.search(r"useradd\s+--system\s+\S+", text), "no system useradd"
    user_lines = [
        (i, line) for i, line in enumerate(text.splitlines(), 1)
        if re.match(r"^USER\s+", line)
    ]
    assert user_lines, "no USER instruction — container would run as root"
    for _i, line in user_lines:
        assert line.split()[1] != "root", f"line {_i}: USER root is forbidden (C4)"
    # USER must come after the last COPY so the copied code is already owned.
    last_copy = max(
        m.start() for m in re.finditer(r"^COPY\s", text, re.MULTILINE)
    )
    user_pos = re.search(r"^USER\s+\S+\s*$", text, re.MULTILINE)
    assert user_pos and user_pos.start() > last_copy, "USER must follow the code COPY layers"
    # The venv + workdir built as root must be handed to the app user.
    assert re.search(r"chown\s+-R\s+\S+\s+/srv/app", text), (
        "missing chown of /srv/app after uv sync (C4)"
    )
    # Code copies must be owned by the runtime user from the start. (The
    # deps COPY of pyproject/uv.lock is covered by the chown -R above.)
    code_copies = [
        line for line in text.splitlines()
        if re.match(r"^COPY\s", line) and "--from=" not in line and "pyproject" not in line
    ]
    assert code_copies, "no code COPY layers found — parser drift?"
    for line in code_copies:
        assert "--chown=" in line, f"code COPY without --chown: {line}"


def test_dockerfile_uses_uv() -> None:
    text = _dockerfile()
    assert "uv" in text
    # uv installed via the official installer, not pip
    assert not re.search(r"^\s*pip\s+install", text, re.MULTILINE)


def test_web_runs_migrations_on_start() -> None:
    compose = _compose()
    web_cmd = compose["services"]["web"].get("command", "")
    assert "alembic upgrade head" in web_cmd


def test_copy_sources_exist_and_are_tracked() -> None:
    """Every COPY/ADD source must exist on disk AND be git-tracked.

    Docker COPY silently fails on empty/untracked dirs (the .dockerignore
    or a fresh clone may lack them), breaking `docker compose build` far
    from the change that caused it. This guard fails fast in unit tests.
    """
    if not (PROJECT_ROOT / ".git").exists():
        pytest.skip(
            "no .git directory: sources cannot be verified as git-tracked "
            "outside a repository checkout (e.g. an exported tree); "
            "run from a real clone to enable this guard"
        )
    text = _dockerfile()
    copy_lines = [
        line for line in text.splitlines()
        if line.strip().upper().startswith(("COPY ", "ADD "))
    ]
    assert copy_lines, "no COPY/ADD instructions found — parser drift?"

    tracked = set(
        subprocess.run(
            ["git", "ls-files"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    )

    problems: list[str] = []
    for line in copy_lines:
        # Skip COPY --from=<stage> lines: their source is a build stage,
        # not a path in the build context.
        if "--from=" in line:
            continue
        parts = line.split()
        # Strip COPY/ADD keyword; sources are all parts except the last (dest).
        sources = parts[1:-1]
        for src in sources:
            if src.startswith("--"):
                continue  # flags like --chown
            path = PROJECT_ROOT / src
            if not path.exists():
                problems.append(f"{src}: does not exist")
                continue
            if path.is_dir():
                # Directory: at least one tracked file must live inside it.
                prefix = src.rstrip("/") + "/"
                if not any(t.startswith(prefix) for t in tracked):
                    problems.append(f"{src}: exists but nothing inside is git-tracked")
            elif src not in tracked:
                problems.append(f"{src}: not git-tracked")
    assert not problems, "COPY/ADD source problems:\n" + "\n".join(problems)
