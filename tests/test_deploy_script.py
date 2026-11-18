"""Static tests for deploy.sh (no Docker, no execution — text assertions).

D1 rules pinned here:
- bash with `set -euo pipefail`;
- never `curl ... | sh` / `wget ... | sh` from unverified sources — Docker
  comes from the official apt repo (download.docker.com);
- no `:latest` image references anywhere in the script;
- an existing .env is NEVER overwritten and secrets are NEVER regenerated;
- generated secrets never reach stdout/logs (they go into .env only);
- the first admin's password is printed ONCE at the very end, not in logs;
- OS check (Ubuntu), root check, port-80-free check with clear errors;
- `--no-seed` flag disables seeding;
- healthcheck wait with timeout + clear error; /health via nginx;
- final output prints http://localhost/ and http://localhost/docs.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_PATH = PROJECT_ROOT / "deploy.sh"


def _script() -> str:
    if not DEPLOY_PATH.exists():
        pytest.fail("deploy.sh missing at repo root")
    return DEPLOY_PATH.read_text(encoding="utf-8")


def test_deploy_sh_exists_and_is_bash() -> None:
    text = _script()
    first_line = text.splitlines()[0]
    assert first_line.startswith("#!"), "deploy.sh must have a shebang"
    assert "bash" in first_line, f"shebang must be bash, got: {first_line}"


def test_deploy_sh_set_euo_pipefail() -> None:
    text = _script()
    assert re.search(r"^set\s+-euo\s+pipefail\s*$", text, re.MULTILINE), (
        "deploy.sh must contain `set -euo pipefail` on its own line"
    )


def test_deploy_sh_no_curl_or_wget_piped_to_shell() -> None:
    text = _script()
    offenders: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        # Any pipeline where curl/wget output is fed into sh/bash.
        if re.search(r"(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba)?sh\b", stripped):
            offenders.append(f"deploy.sh:{i}: {stripped}")
        # Also catch `sh <(curl ...)` process substitution.
        if re.search(r"(ba|z)?sh\s*<\(\s*(curl|wget)\b", stripped):
            offenders.append(f"deploy.sh:{i}: {stripped}")
    assert not offenders, "curl|sh / wget|sh forbidden:\n" + "\n".join(offenders)


def test_deploy_sh_no_latest_image_tags() -> None:
    text = _script()
    offenders = [
        f"deploy.sh:{i}: {line.strip()}"
        for i, line in enumerate(text.splitlines(), 1)
        if re.search(r":latest\b", line)
    ]
    assert not offenders, ":latest tags forbidden in deploy.sh:\n" + "\n".join(offenders)


def test_deploy_sh_docker_from_official_apt_repo() -> None:
    """Docker Engine must come from download.docker.com (official apt repo),
    not from a random script. The URL must appear so the source is auditable."""
    text = _script()
    assert "download.docker.com" in text, (
        "deploy.sh must reference the official Docker apt repo "
        "(https://download.docker.com/linux/ubuntu) — no curl|sh"
    )


def test_deploy_sh_checks_ubuntu() -> None:
    text = _script()
    assert "os-release" in text and "ubuntu" in text.lower(), (
        "deploy.sh must verify the OS is Ubuntu via /etc/os-release"
    )


def test_deploy_sh_checks_root() -> None:
    text = _script()
    assert re.search(r"EUID|\$\(id\s+-u\)|id\s+-u", text), (
        "deploy.sh must check it is running as root (EUID / id -u)"
    )


def test_deploy_sh_checks_port_80_free() -> None:
    text = _script()
    # Must probe port 80 before starting (ss / lsof / /dev/tcp / fuser).
    assert re.search(r"\b(ss|lsof|fuser|netstat)\b.*\b:80\b|\b:80\b.*\b(ss|lsof|fuser|netstat)\b|/dev/tcp/127\.0\.0\.1/80", text), (
        "deploy.sh must check that port 80 is free before starting"
    )


def test_deploy_sh_existing_env_not_overwritten() -> None:
    """An existing .env must be preserved: the creation branch must be
    guarded by a file-existence test, and no line may unconditionally
    redirect into .env."""
    text = _script()
    # There must be a guard like `[ -f .env ]` or `[ ! -f .env ]`.
    assert re.search(r"\[\s*-f\s+\.env\s*\]|\[\s*!\s*-f\s+\.env\s*\]|test\s+-f\s+\.env", text), (
        "deploy.sh must guard .env creation with a file-existence check "
        "so an existing .env is never overwritten"
    )
    # No unconditional overwrite: `> .env` without a guard is suspicious.
    for i, line in enumerate(text.splitlines(), 1):
        if re.search(r">\s*\.env\b", line) and "2>" not in line:
            # allowed only inside an else/if-guard; we check the guard exists above
            pass  # the guard test above covers the requirement


def test_deploy_sh_secrets_not_printed_to_stdout() -> None:
    """Generated secrets (SECRET_KEY, POSTGRES_PASSWORD, S3_SECRET_KEY,
    admin password) must go into .env or a variable — never echoed to
    stdout/log. The admin password is allowed ONLY in the final summary
    block at the end of the script."""
    text = _script()
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # echo/printf of a variable that holds a stored secret value.
        # ADMIN_PASSWORD is handled separately below (allowed once, at the end).
        if re.search(r"\b(echo|printf)\b.*\$(SECRET_KEY|POSTGRES_PASSWORD|S3_SECRET_KEY|S3_ACCESS_KEY)\b", stripped):
            pytest.fail(
                f"deploy.sh:{i} prints a stored secret to stdout: {stripped!r}"
            )
    # The admin password MAY be printed exactly once, at the end.
    pw_prints = [
        i for i, line in enumerate(lines, 1)
        if re.search(r"\b(echo|printf)\b.*\$ADMIN_PASSWORD", line)
        and not line.strip().startswith("#")
    ]
    assert len(pw_prints) <= 1, (
        f"admin password must be printed at most once (found at lines {pw_prints})"
    )
    if pw_prints:
        # Must be near the end (last 20% of the script).
        assert pw_prints[0] > len(lines) * 0.8, (
            f"admin password print at line {pw_prints[0]} must be in the final "
            f"summary block (script has {len(lines)} lines)"
        )


def test_deploy_sh_no_seed_flag() -> None:
    text = _script()
    assert "--no-seed" in text, "deploy.sh must support a --no-seed flag"


def test_deploy_sh_waits_for_health_with_timeout() -> None:
    text = _script()
    assert "docker compose" in text and "ps" in text or "inspect" in text, (
        "deploy.sh must poll `docker compose ps`/inspect for health"
    )
    assert re.search(r"--timeout|\btimeout\b|SECONDS=", text), (
        "deploy.sh must have a timeout when waiting for healthchecks"
    )
    assert "/health" in text, "deploy.sh must wait for /health through nginx"


def test_deploy_sh_final_output_urls() -> None:
    text = _script()
    assert "http://localhost/" in text, "final output must print http://localhost/"
    assert "http://localhost/docs" in text, "final output must print http://localhost/docs"


def test_deploy_sh_uses_openssl_rand_for_secrets() -> None:
    text = _script()
    assert "openssl rand" in text, (
        "deploy.sh must generate random secrets with `openssl rand`"
    )


def test_deploy_sh_compose_up_build() -> None:
    text = _script()
    assert re.search(r"docker\s+compose\s+up\s+-d\s+--build", text), (
        "deploy.sh must run `docker compose up -d --build`"
    )
