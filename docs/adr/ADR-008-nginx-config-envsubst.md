# ADR-008: nginx config as envsubst template, substitution limited to `CSP_`

Status: accepted (2026-11-16). Context: real-stack check after cuts F1–F7 —
nginx restart-loops, site down, `[emerg] 1#1: unknown "csp_media_origin"
variable`; `20-envsubst-on-templates.sh` in the entrypoint ran idle because
no `*.template` file existed. Other containers were fine, migrations applied.

## Decision

The nginx site config ships as a **template rendered by the official image's
own entrypoint**, not as a ready-to-use config:

1. `nginx/default.conf.template` is mounted read-only at
   `/etc/nginx/templates/default.conf.template`; the entrypoint
   (`20-envsubst-on-templates.sh`) renders it to
   `/etc/nginx/conf.d/default.conf` on every start.
2. **`NGINX_ENVSUBST_FILTER=^CSP_`** — only variables whose names start with
   `CSP_` are substituted. All nginx variables (`$host`, `$remote_addr`,
   `$http_host`, `$scheme`, `$binary_remote_addr`, `$proxy_add_x_forwarded_for`,
   `$http_range`) stay literal for nginx.
3. **The scheme is never hardcoded.** `docker-compose.yml` assembles the
   browser-visible media origin from two variables:
   `CSP_MEDIA_ORIGIN=<CSP_MEDIA_SCHEME>://<S3_PUBLIC_ENDPOINT>` (endpoint may
   carry `:port`). Defaults are `http` + `localhost`, so a local
   `docker compose up` without `deploy.sh`/`.env` works out of the box; on the
   VM `CSP_MEDIA_SCHEME=https` covers TLS offloading. `S3_PUBLIC_ENDPOINT` is
   the single source of the browser-visible storage host (same value the
   presigned-URL client gets from the app).

Consequences / rules:

1. Guard test `tests/test_nginx_template_render.py` renders the template the
   way the container does (substitution restricted by the filter) and fails on
   unexpanded `${...}`, unknown nginx variables, a mount straight into
   `/etc/nginx/conf.d/`, or a hardcoded scheme in the CSP header. `nginx -t`
   on the rendered config lives behind the `integration` marker.
2. Values reaching nginx are exactly the compose-composed `CSP_MEDIA_ORIGIN`;
   if it were unset, envsubst would substitute an empty string — the guard
   renders with explicit envs so an empty origin cannot ship silently.
3. The previous failure mode is structurally impossible: a mount target of
   `/etc/nginx/conf.d/` would leave `${CSP_MEDIA_ORIGIN}` literal, and the
   guard forbids that mount target.
4. The CSP header value is defined in one place (the template). Directive set:
   `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'
   data:; media-src 'self' <origin>; connect-src 'self'; font-src 'self';
   form-action 'self'; frame-ancestors 'none'; object-src 'none';
   base-uri 'self'`.

## Alternatives considered

- Mount `nginx.conf` directly into `/etc/nginx/conf.d/` (rejected — this was
  the bug: the image substitutes env vars only into files from
  `/etc/nginx/templates/*.template`).
- `envsubst` the whole file without a filter (rejected — it would eat every
  `$variable` whose name matches any environment variable, breaking nginx
  variables like `$host` and `$remote_addr`).
- Declare `$$CSP_MEDIA_ORIGIN` as an nginx variable fed from the environment
  (rejected — nginx never exposes environment variables as nginx variables;
  only the template mechanism substitutes them).
- Own entrypoint wrapper with sed (rejected — reinvents what the official
  image entrypoint already does, adds a build dependency).