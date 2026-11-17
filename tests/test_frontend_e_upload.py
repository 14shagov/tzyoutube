"""E: upload status polling contract + behaviour.

Red on the buggy code: upload.js calls window.tzApi.get(...) but app.js only
defines .post/.del/.patch, so the poll throws inside setInterval and ZERO
GET /api/videos/{id}/status requests ever fire (page stuck "обрабатывается").
Also pins: polling stops on ready AND failed, shows failure_reason, has a
timeout message, and the response models contain the fields JS reads.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "app" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
UPLOAD_JS = (STATIC / "upload.js").read_text(encoding="utf-8")


def test_app_js_defines_tzapi_get():
    assert re.search(r"window\.tzApi\.get\s*=\s*function", APP_JS), (
        "app.js must define window.tzApi.get — upload.js polls with tzApi.get"
    )


def test_upload_poll_stops_on_ready_and_failed_and_shows_failure_reason():
    ready_block = re.search(r'status\s*===\s*"ready".*?clearInterval', UPLOAD_JS, re.S)
    failed_block = re.search(r'status\s*===\s*"failed".*?clearInterval', UPLOAD_JS, re.S)
    assert ready_block, "poll must clearInterval on ready"
    assert failed_block, "poll must clearInterval on failed"
    assert "failure_reason" in UPLOAD_JS, "failed message must include failure_reason"


def test_upload_poll_has_timeout_message():
    assert "Превышено время ожидания" in UPLOAD_JS, (
        "poll must abort with a clear message after a timeout"
    )


def _schema_for(path: str, method: str, status: str) -> dict:
    from app.main import create_app

    spec = create_app().openapi()
    op = spec["paths"][path][method.lower()]
    ref = op["responses"][status]["content"]["application/json"]["schema"]
    while "$ref" in ref:
        ref = spec["components"]["schemas"][ref["$ref"].split("/")[-1]]
    return ref


def test_status_route_response_has_fields_js_reads():
    schema = _schema_for("/api/videos/{video_id}/status", "GET", "200")
    props = set(schema.get("properties", {}))
    assert {"status", "failure_reason"} <= props, (
        f"GET /api/videos/{{id}}/status must return status+failure_reason, got {props}"
    )


def test_upload_202_response_has_id():
    schema = _schema_for("/api/videos", "POST", "202")
    props = set(schema.get("properties", {}))
    assert "id" in props, f"POST /api/videos 202 must return id, got {props}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_upload_js_poll_behaviour_node():
    r = subprocess.run(
        ["node", "--test", "tests/js/upload.test.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
