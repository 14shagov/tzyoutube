"""Guard: the nginx config ships as an envsubst template that renders exactly
like the official image does it, and compose mounts it as a template.

Real-stack bug 2026-11-16 (after cuts F1-F7): nginx/nginx.conf was mounted
straight into /etc/nginx/conf.d/ while carrying a literal `$CSP_MEDIA_ORIGIN`.
The nginx image substitutes env vars ONLY into files from
/etc/nginx/templates/*.template, so the placeholder survived, nginx treated
`$CSP_MEDIA_ORIGIN` as its own variable and died on every start:
    [emerg] 1#1: unknown "csp_media_origin" variable

The renderer below mirrors docker-nginx's entrypoint
(`20-envsubst-on-templates.sh`): env vars matching NGINX_ENVSUBST_FILTER are
substituted (both `${NAME}` and `$NAME` forms), every other $-variable stays
literal for nginx. Integration variants (real `nginx -t` / real `envsubst`)
wait for the binaries to exist; the unit tests need nothing but Python.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "nginx" / "default.conf.template"
COMPOSE_PATH = ROOT / "docker-compose.yml"

# nginx image: NGINX_ENVSUBST_FILTER selects which env vars get substituted.
ENVSUBST_FILTER_SOURCE = "^CSP_"
_ENVSUBST_FILTER = re.compile(ENVSUBST_FILTER_SOURCE)

# $variables nginx itself provides in this config - must survive rendering.
NGINX_VARIABLES = frozenset(
    {
        "binary_remote_addr",
        "http_host",
        "http_range",
        "proxy_add_x_forwarded_for",
        "remote_addr",
        "scheme",
    }
)

# ${NAME} and $NAME forms, like `envsubst '${CSP_A} ${CSP_B}'` expands.
_VAR = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
)

# Local `docker compose up` without deploy.sh/.env must work: defaults.
DEFAULT_ENV = {"CSP_MEDIA_ORIGIN": "http://localhost"}
# Doc-range placeholder (AGENTS.md) with a port, e.g. behind TLS offloading.
CUSTOM_ENV = {"CSP_MEDIA_ORIGIN": "https://203.0.113.10:8443"}


def render_like_container(text: str, env: dict[str, str]) -> str:
    """Mirror 20-envsubst-on-templates.sh: substitute ONLY variables matching
    NGINX_ENVSUBST_FILTER, leave everything else literal for nginx."""

    def replace(match: re.Match[str]) -> str:
        name = match.group("braced") or match.group("bare")
        if _ENVSUBST_FILTER.match(name):
            return env.get(name, "")  # envsubst: unset listed var -> empty
        return match.group(0)

    return _VAR.sub(replace, text)


def template_text() -> str:
    if not TEMPLATE_PATH.exists():
        pytest.fail(
            f"{TEMPLATE_PATH} missing - the nginx config must ship as an "
            f"envsubst template (mounted into /etc/nginx/templates/)"
        )
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def compose_doc() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def nginx_service() -> dict:
    return compose_doc()["services"]["nginx"]


def nginx_volumes() -> list[str]:
    return nginx_service().get("volumes", [])


def csp_directives(rendered: str) -> dict[str, str]:
    """Parse `add_header Content-Security-Policy "..."` into a directive map."""
    match = re.search(r"Content-Security-Policy\s+\"([^\"]+)\"", rendered)
    assert match, "no Content-Security-Policy header in rendered config"
    directives = {}
    for part in match.group(1).split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition(" ")
        directives[name] = value.strip()
    return directives


def test_template_file_exists() -> None:
    template_text()


def test_render_leaves_no_unexpanded_placeholders() -> None:
    for env in (DEFAULT_ENV, CUSTOM_ENV):
        rendered = render_like_container(template_text(), env)
        assert not re.search(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", rendered), (
            f"unexpanded ${{...}} placeholder with env={env}"
        )


def _strip_comment_lines(text: str) -> str:
    """Drop comments: a `$variable` name mentioned in prose is data, not a
    placeholder — but check for `$` at line start or after whitespace."""
    return "".join(
        line if not line.lstrip().startswith("#") else ""
        for line in text.splitlines(keepends=True)
    )


def test_render_keeps_nginx_variables_and_has_no_unknown_ones() -> None:
    rendered = render_like_container(template_text(), DEFAULT_ENV)
    directive_text = _strip_comment_lines(rendered)
    names = {m.group("braced") or m.group("bare") for m in _VAR.finditer(directive_text)}
    assert names, "no $-variables survived - renderer or template is broken"
    unknown = sorted(names - NGINX_VARIABLES)
    assert not unknown, f"unknown nginx variables in rendered config: {unknown}"


def test_nginx_variables_survive_render_verbatim() -> None:
    rendered = render_like_container(template_text(), DEFAULT_ENV)
    for var in NGINX_VARIABLES:
        assert f"${var}" in rendered, f"${var} was eaten by the renderer"


def test_csp_header_carries_media_origin() -> None:
    default = csp_directives(render_like_container(template_text(), DEFAULT_ENV))
    assert default["media-src"] == "'self' http://localhost"
    assert "'self'" in default["img-src"] and "data:" in default["img-src"]

    custom = csp_directives(render_like_container(template_text(), CUSTOM_ENV))
    assert custom["media-src"] == "'self' https://203.0.113.10:8443"


def test_csp_directive_set_matches_owner_spec() -> None:
    directives = csp_directives(render_like_container(template_text(), DEFAULT_ENV))
    expected = {
        "default-src": "'self'",
        "script-src": "'self'",
        "style-src": "'self'",
        "img-src": "'self' data:",
        "media-src": "'self' http://localhost",
        "connect-src": "'self'",
        "font-src": "'self'",
        "form-action": "'self'",
        "frame-ancestors": "'none'",
        "object-src": "'none'",
        "base-uri": "'self'",
    }
    assert directives == expected


def test_template_does_not_hardcode_the_scheme() -> None:
    csp_line = next(
        line
        for line in template_text().splitlines()
        if "Content-Security-Policy" in line
    )
    assert "http://" not in csp_line and "https://" not in csp_line, (
        f"hardcoded scheme in CSP header: {csp_line}"
    )


def test_compose_mounts_template_into_etc_nginx_templates() -> None:
    volumes = nginx_volumes()
    assert volumes, "nginx service has no volumes"
    targets = []
    for entry in volumes:
        parts = entry.split(":")
        targets.append(parts[1])
    for target in targets:
        assert not target.startswith("/etc/nginx/conf.d/"), (
            f"mount into {target} skips envsubst - use /etc/nginx/templates/"
        )
    assert any(t.startswith("/etc/nginx/templates/") for t in targets), (
        "no mount into /etc/nginx/templates/ - the entrypoint would stay idle"
    )


def test_compose_sets_envsubst_filter_limited_to_csp() -> None:
    env = nginx_service().get("environment", {})
    subscribe_pattern = env.get("NGINX_ENVSUBST_FILTER")
    assert subscribe_pattern == ENVSUBST_FILTER_SOURCE
    for name in env:
        if name.startswith("CSP_"):
            assert _ENVSUBST_FILTER.match(name), f"{name} would not be substituted"


def test_compose_builds_media_origin_from_scheme_and_endpoint() -> None:
    env = nginx_service().get("environment", {})
    origin = env.get("CSP_MEDIA_ORIGIN", "")
    assert (
        "${CSP_MEDIA_SCHEME:-" in origin
    ), "CSP_MEDIA_ORIGIN must be built from a scheme variable"
    assert (
        "${S3_PUBLIC_ENDPOINT:-" in origin
    ), "CSP_MEDIA_ORIGIN must include S3_PUBLIC_ENDPOINT"


@pytest.mark.integration
def test_nginx_accepts_the_rendered_template() -> None:
    """Render the template and syntax-check it with the real binary.

    Upstream hosts (web, silo) are rewritten to loopback: docker network names
    do not resolve on the test host, and `nginx -t` fails on unknown upstream
    names before checking anything else. Grammar and structure are what we
    validate here.
    """
    if shutil.which("nginx") is None:
        pytest.skip("nginx binary not available")
    rendered = render_like_container(template_text(), DEFAULT_ENV)
    rendered = rendered.replace("http://web:8000", "http://127.0.0.1:8000")
    rendered = rendered.replace("http://silo:9000", "http://127.0.0.1:9000")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = tmp_path / "rendered.conf"
        wrapper = tmp_path / "wrapper.conf"
        config.write_text(rendered, encoding="utf-8")
        wrapper.write_text(
            f"pid {tmp_path / 'nginx.pid'};\n"
            f"error_log {tmp_path / 'error.log'};\n"
            "events {}\n"
            f"http {{ include {config}; }}\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            ["nginx", "-t", "-c", str(wrapper), "-p", str(tmp_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert "syntax is ok" in result.stderr + result.stdout, result.stderr


@pytest.mark.integration
def test_renderer_matches_real_envsubst() -> None:
    """If `envsubst` is on PATH, the mirror above must equal the real tool."""
    if shutil.which("envsubst") is None:
        pytest.skip("envsubst binary not available")
    for env in (DEFAULT_ENV, CUSTOM_ENV):
        names = " ".join(f"${{{name}}}" for name in env)
        result = subprocess.run(
            ["envsubst", names],
            check=False,
            input=template_text(),
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, **env},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == render_like_container(template_text(), env), (
            f"renderer divergence with env={env}"
        )