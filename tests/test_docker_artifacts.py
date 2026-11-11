"""Unit tests for Docker artifacts (no Docker needed to run these).

They parse the YAML and assert the compose file declares the expected
services, and that the Dockerfile references the uv-based workflow.
"""

import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_compose_declares_expected_services() -> None:
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    for name in ("postgres", "redis", "silo", "web", "worker", "nginx"):
        assert name in services, f"service {name} missing in docker-compose.yml"


def test_compose_publishes_only_nginx() -> None:
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for name, svc in compose["services"].items():
        ports = svc.get("ports")
        if ports:
            for published in ports:
                # published entries like "8080:80" — only nginx may expose ports
                assert name == "nginx", f"{name} publishes ports: {ports}"


def test_dockerfile_uses_uv() -> None:
    text = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "uv" in text
    # uv installed via the official installer, not pip
    assert not re.search(r"^\s*pip\s+install", text, re.MULTILINE)


def test_web_runs_migrations_on_start() -> None:
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    web_cmd = compose["services"]["web"].get("command", "")
    assert "alembic upgrade head" in web_cmd
