"""D: after a successful PATCH/POST the page shows «Сохранено» (status
element) and then reloads/updates; on error the API error text is shown.

Covers video edit / privacy / ACL (manage.js + my_videos.html) and the
channel edit form (channel.js + channel.html). Static checks here; the
behavioural check runs the real JS under `node --test` with a stubbed
fetch/DOM (skipped when node is absent).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "app" / "static"
TEMPLATES = ROOT / "src" / "app" / "templates"


def test_my_videos_template_has_row_status_and_edit_row_id() -> None:
    html = (TEMPLATES / "my_videos.html").read_text(encoding="utf-8")
    assert 'class="save-status"' in html, "row needs a save-status element"
    # edit-row must carry data-video-id so the submit handler can find its row
    assert 'class="edit-row" data-video-id=' in html, (
        "edit-row without data-video-id: submit handler's closest() finds no row"
    )


def test_manage_js_uses_status_element_for_success_and_error() -> None:
    js = (STATIC / "manage.js").read_text(encoding="utf-8")
    assert ".save-status" in js, "handler must address the status element"
    assert "Сохранено" in js, "success must show «Сохранено»"
    assert "showStatus" in js
    assert "err.message" in js, "error path must show the API error text"


def test_channel_template_and_js_have_status_element() -> None:
    html = (TEMPLATES / "channel.html").read_text(encoding="utf-8")
    assert 'id="channel-edit-status"' in html
    js = (STATIC / "channel.js").read_text(encoding="utf-8")
    assert "channel-edit-status" in js
    assert "Сохранено" in js
    assert "err.message" in js


def test_node_js_behaviour_tests_pass() -> None:
    if shutil.which("node") is None:
        pytest.skip("node not available")
    r = subprocess.run(
        ["node", "--test", "tests/js/"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stdout + "\n" + r.stderr
