"""C5: nginx perimeter hardening — security headers, auth rate limit,
512m body cap ONLY on the upload location, unbuffered upload proxying.

Static parse of nginx/nginx.conf (no nginx needed in unit tests).
"""

import re
from pathlib import Path

NGINX_CONF = Path(__file__).resolve().parents[1] / "nginx" / "nginx.conf"


def _text() -> str:
    return NGINX_CONF.read_text(encoding="utf-8")


def _block(text: str, marker: str) -> str:
    """Extract the {...} block starting at the line containing marker."""
    i = text.index(marker)
    j = text.index("{", i)
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[j : k + 1]
    raise AssertionError(f"unbalanced braces after {marker!r}")


def _server_block(text: str) -> str:
    return _block(text, "server {")


def test_security_headers_on_server() -> None:
    server = _server_block(_text())
    assert re.search(r"add_header\s+X-Content-Type-Options\s+nosniff\s+always", server)
    assert re.search(r"add_header\s+X-Frame-Options\s+\S+\s+always", server)
    assert re.search(r"add_header\s+Referrer-Policy\s+\S+\s+always", server)


def test_512m_only_on_upload_location() -> None:
    text = _text()
    # exactly one 512m directive in the whole config
    assert len(re.findall(r"client_max_body_size\s+512m", text)) == 1
    upload = _block(text, "location = /api/videos")
    assert re.search(r"client_max_body_size\s+512m", upload)
    # upload is proxied unbuffered so the app can abort mid-body on size
    assert re.search(r"proxy_request_buffering\s+off", upload)
    assert "proxy_pass http://web:8000" in upload
    # the default cap for everything else is much smaller
    server = _server_block(text)
    m = re.search(r"client_max_body_size\s+(\d+)([km]?)", server)
    assert m, "server-level client_max_body_size missing"
    assert m.group(0) != "client_max_body_size 512m"


def test_auth_location_rate_limited() -> None:
    text = _text()
    # zone defined at http level (this file is included into conf.d)
    assert re.search(r"limit_req_zone\s+\$binary_remote_addr\s+zone=auth\S*:\d+m\s+rate=\S+", text)
    auth = _block(text, "location /auth/")
    assert re.search(r"limit_req\s+zone=auth\S*", auth)
    assert "proxy_pass http://web:8000" in auth


def test_x_real_ip_contract_preserved() -> None:
    # The app trusts X-Real-IP ONLY because nginx overwrites it with
    # $remote_addr; an additive header would be client-spoofable.
    text = _text()
    assert "proxy_set_header X-Real-IP $remote_addr;" in text
    assert "proxy_set_header X-Real-IP $proxy_add_x_forwarded_for" not in text


def test_silo_locations_keep_host_and_buffering() -> None:
    text = _text()
    videos = _block(text, "location /videos/")
    assert "proxy_set_header Host $http_host;" in videos
    assert re.search(r"proxy_request_buffering\s+off", videos)
    thumbs = _block(text, "location /thumbnails/")
    assert "proxy_set_header Host $http_host;" in thumbs
