"""JS-API contract guard (bug A): every URL literal that static/*.js sends
via fetch / XMLHttpRequest / tzApi / location.href, and every /api|//auth URL
in templates (form actions, href/src), must match a route + method from
app.openapi() (path parameters accounted for). SSR page links must match the
pages router.

Red on the buggy code: app.js logout POST /api/auth/logout and auth.js
POST /api/auth/{register,login} do not exist in openapi() — real auth routes
live under /auth, not /api/auth.
"""

import re
from pathlib import Path

from app import pages as pages_api
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "app" / "static"
TEMPLATES = ROOT / "src" / "app" / "templates"


def _api_routes() -> list[tuple[str, str]]:
    """(METHOD, path_template) for every /api and /auth route in openapi()."""
    spec = create_app().openapi()
    out = []
    for path, ops in spec["paths"].items():
        if path.startswith(("/api", "/auth")):
            for method in ops:
                out.append((method.upper(), path))
    return out


def _page_routes() -> list[tuple[str, str]]:
    """(METHOD, path_template) for SSR page routes (include_in_schema=False)."""
    out = []
    for r in pages_api.router.routes:
        methods = getattr(r, "methods", None) or {"GET"}
        for m in methods:
            out.append((m.upper(), r.path))
    return out


def _sample(template: str) -> str:
    """'/api/videos/{video_id}/status' -> '/api/videos/x/status'."""
    return re.sub(r"\{[^}]+\}", "x", template)


def _strip_query(pat: str) -> str:
    """Drop an escaped query string (\\?...) from a regex pattern."""
    return pat.split("\\?")[0]


_BREAK = ")]},;"


def _read_path_expr(source: str, i: int) -> str:
    """Scan a JS path expression starting at index i: string literals and other
    operands joined by '+'. String literals are regex-escaped; every non-string
    operand becomes a [^/]+ wildcard segment. Stops at ',', ')', ']', '}' or ';'."""
    parts: list[str] = []
    n = len(source)
    while i < n:
        ch = source[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == "+":
            i += 1
            continue
        if ch in _BREAK:
            break
        if ch in "\"'":
            q = ch
            i += 1
            buf: list[str] = []
            while i < n and source[i] != q:
                if source[i] == "\\" and i + 1 < n:
                    buf.append(source[i + 1])
                    i += 2
                else:
                    buf.append(source[i])
                    i += 1
            i += 1  # closing quote
            parts.append(re.escape("".join(buf)))
            continue
        # non-string operand (variable, ternary, call) -> one wildcard segment
        depth = 0
        while i < n:
            c = source[i]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif c in "+," and depth == 0:
                break
            elif c == ";" and depth == 0:
                break
            i += 1
        parts.append("[^/]+")
    return "".join(parts)


def _string_literals_in(source: str, start: int, end: int) -> list[str]:
    """All quoted string literals in source[start:end] that start with '/'."""
    out = []
    for m in re.finditer(r"[\"'](/[^\"']*)[\"']", source[start:end]):
        out.append(re.escape(m.group(1)))
    return out


_TZ_SHORT = {"post": "POST", "del": "DELETE", "patch": "PATCH", "get": "GET"}


def _js_calls(source: str) -> list[tuple[str, str, str]]:
    """Yield (method, path_regex, snippet) for URL-bearing calls in a JS file."""
    calls: list[tuple[str, str, str]] = []
    # xhr.open("METHOD", "PATH" ...)
    for m in re.finditer(r"\.open\s*\(\s*[\"']([A-Za-z]+)[\"']\s*,\s*", source):
        pat = _strip_query(_read_path_expr(source, m.end()))
        if pat.startswith("/"):
            calls.append((m.group(1).upper(), pat, source[m.start(): m.start() + 90].split("\n")[0]))
    # tzApi.post/del/patch/get("PATH") | tzApi("PATH", {method}) | fetch("PATH")
    for m in re.finditer(r"(?:tzApi(?:\.(post|del|patch|get))?|fetch)\s*\(\s*", source):
        tail = source[m.start(): m.start() + 140]
        if m.group(1):
            method = _TZ_SHORT[m.group(1)]
        else:
            mm = re.search(r"method\s*:\s*[\"']([A-Za-z]+)[\"']", tail)
            method = mm.group(1).upper() if mm else "GET"
        arg_start = m.end()
        pat = _strip_query(_read_path_expr(source, arg_start))
        if pat.startswith("/"):
            calls.append((method, pat, tail.split("\n")[0]))
        else:
            # first operand was not a string literal (e.g. a ternary):
            # check every '/...' literal inside the argument expression
            region_end = min(len(source), arg_start + 200)
            for lit in _string_literals_in(source, arg_start, region_end):
                calls.append((method, _strip_query(lit), tail.split("\n")[0]))
    # window.location.href = "PATH"
    for m in re.finditer(r"location\.href\s*=\s*", source):
        j = m.end()
        while j < len(source) and source[j] in " \t":
            j += 1
        if j < len(source) and source[j] in "\"'":
            pat = _strip_query(_read_path_expr(source, j))
            if pat.startswith("/"):
                calls.append(("GET", pat, source[m.start(): m.start() + 90].split("\n")[0]))
    return calls


def _template_urls(source: str) -> list[tuple[str, str, str]]:
    """Yield (method, path_regex, raw) for href=/action=/src= absolute paths."""
    out: list[tuple[str, str, str]] = []
    for m in re.finditer(r"\b(href|action|src)\s*=\s*\"([^\"]*)\"", source):
        attr, raw = m.group(1), m.group(2)
        if not raw.startswith("/") or raw.startswith(("/static/", "/media/")):
            continue
        path = raw.split("?", 1)[0].split("#", 1)[0]
        path = re.sub(r"\{%[^%]*%\}", "", path)          # jinja statements
        path = re.sub(r"\{\{[^}]*\}\}", "\x00", path)     # jinja vars -> placeholder
        pat = "^" + re.escape(path).replace("\x00", "[^/]+") + "$"
        method = "GET"
        if attr == "action":
            seg = source[max(0, m.start() - 300): m.end() + 10]
            mm = re.search(r"method\s*=\s*\"([A-Za-z]+)\"", seg)
            if mm:
                method = mm.group(1).upper()
        out.append((method, pat, raw))
    return out


def test_js_and_template_urls_match_routes_and_methods() -> None:
    api = [(m, _sample(p), p) for m, p in _api_routes()]
    pages = [(m, _sample(p), p) for m, p in _page_routes()]
    failures: list[str] = []

    for f in sorted(STATIC.glob("*.js")):
        for method, pat, snippet in _js_calls(f.read_text(encoding="utf-8")):
            rx = re.compile("^" + pat + "$")
            if pat.startswith(("/api", "/auth")):
                ok = any(m == method and rx.match(s) for m, s, _ in api)
            else:
                ok = any(rx.match(s) for _, s, _ in pages)
            if not ok:
                failures.append(f"{f.name}: {method} {pat!r}  <- {snippet!r}")

    for f in sorted(TEMPLATES.glob("*.html")):
        for method, pat, raw in _template_urls(f.read_text(encoding="utf-8")):
            rx = re.compile(pat)
            if pat.startswith(("^/api", "^/auth")):
                ok = any(m == method and rx.match(s) for m, s, _ in api)
            else:
                ok = any(rx.match(s) for _, s, _ in pages)
            if not ok:
                failures.append(f"{f.name}: {method} {raw!r}")

    assert not failures, (
        "URL literals with no matching route/method:\n" + "\n".join(failures)
    )
