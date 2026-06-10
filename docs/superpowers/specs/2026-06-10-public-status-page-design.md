# Public Status Page (Grafana-backed) — Design

## Goal

Give visitors to `insidedcpulse.com` a public, read-only "system status" page at
`/status`, backed by two existing Grafana dashboards (World Stability Index,
Event Flow Timeline), without exposing the rest of Grafana or requiring
anonymous org-wide access.

## Background

Grafana already runs at `/grafana/` (admin/password protected,
`grafana/grafana:latest`, single org, folder `InsideDCPulse`, 5 provisioned
dashboards). Grafana v11+ ships **Public Dashboards** GA: any individual
dashboard can be marked public, generating a stable per-dashboard
`accessToken` and a URL `/grafana/public-dashboards/<accessToken>` that
renders without authentication — independent of the rest of the org, which
stays login-protected.

## Scope

Public dashboards: `idc-world-stability` (World Stability Index) and
`idc-event-flow-timeline` (Event Flow Timeline). The other three dashboards
(AI Consensus Health, System Drift Meter, Agent Reputation Map) stay
internal/login-only.

## Architecture

```
Browser -> https://insidedcpulse.com/status
             |
             v
        nginx: location /status -> static HTML (docker/nginx/static/status.html)
             |
             v (2 iframes)
        nginx: location /grafana/public-dashboards/<token> -> grafana:3000
```

No new backend code paths beyond a one-line addition to the existing `/`
JSON response for discoverability.

## Components

1. **`docker-compose.yml`** — add `GF_SECURITY_ALLOW_EMBEDDING: "true"` to
   the `grafana` service environment. Grafana sets
   `X-Frame-Options: deny` by default, which blocks the iframes on
   `/status` unless this is set.

2. **`docker/nginx/static/status.html`** — new static file. Minimal
   HTML/CSS page (no JS framework, no build step):
   - Page title "InsideDCPulse — System Status"
   - Short paragraph linking back to `/docs` and explaining this is a
     read-only public view of the World Stability Index and Event Flow
     Timeline dashboards
   - Two `<iframe>` elements, `src="/grafana/public-dashboards/<TOKEN>?kiosk"`
     for each dashboard's access token (tokens filled in during one-time
     setup, see Component 4)
   - Responsive layout: iframes stacked vertically, full width, fixed
     height (e.g. 500px each)

3. **`docker/nginx/conf.d/insidedcpulse.conf.ssl`** — add:
   - New volume mount on the `nginx` service in `docker-compose.yml`:
     `./nginx/static:/usr/share/nginx/html/static:ro`
   - New `location /status` block serving
     `/usr/share/nginx/html/static/status.html`, placed before the
     catch-all `location /` (public API) block so it isn't proxied to the
     API.

4. **`docker/grafana/setup-public-dashboards.sh`** — one-time, idempotent
   shell script using the Grafana HTTP API with admin basic auth
   (`$GRAFANA_ADMIN_PASSWORD`):
   - For each of `idc-world-stability` and `idc-event-flow-timeline`:
     - `GET /api/dashboards/uid/<uid>/public-dashboards` — if a public
       dashboard already exists and `isEnabled: true`, print its
       `accessToken` and skip.
     - Otherwise `POST /api/dashboards/uid/<uid>/public-dashboards` with
       `{"isEnabled": true, "share": "public"}`, print the returned
       `accessToken`.
   - Run manually once after deploy (documented in README). Output tokens
     are pasted into `status.html`'s iframe `src` attributes and committed.

5. **`backend/app/main.py`** — add `"status": "/status"` key to the `root()`
   JSON response (`main.py:66-73`), alongside existing `docs` and
   `world_stream` keys, for discoverability.

6. **README** — document `/status` as a public endpoint and briefly
   describe the one-time public-dashboard setup script for future
   reference (e.g. if dashboards are recreated/UIDs change).

## Data Flow

1. Admin runs `setup-public-dashboards.sh` once (manually, against the live
   Grafana instance) — produces two access tokens.
2. Tokens are pasted into `status.html`, committed, deployed via the
   existing webhook auto-deploy (rebuilds `nginx`'s static content via the
   new volume mount — no image rebuild needed for `nginx`, just a restart
   if the volume mount itself is new; subsequent edits to `status.html`
   take effect on nginx reload/restart).
3. Visitors hit `/status` → nginx serves the static page → browser loads
   the two iframes directly from `/grafana/public-dashboards/<token>?kiosk`
   → nginx proxies to Grafana, which serves the public (unauthenticated)
   dashboard view.

## Error Handling / Edge Cases

- If the setup script hasn't been run yet (tokens still placeholders),
  iframes will show a Grafana "not found" error inside the iframe — page
  itself still loads. This is an acceptable transient state during rollout,
  not a runtime failure mode.
- `/status` itself requires no auth and has no dynamic behavior — nothing to
  validate at request time.
- If a public dashboard is later disabled/deleted in Grafana (UI), the
  corresponding iframe breaks until `setup-public-dashboards.sh` is re-run
  and `status.html` updated with a new token.

## Testing

This is static infra, not application logic — no new pytest tests.
Validation is manual, post-deploy:

- `curl -s -o /dev/null -w "%{http_code}" https://insidedcpulse.com/status`
  → `200`
- `curl -s https://insidedcpulse.com/status | grep -c iframe` → `2`
- `curl -s -o /dev/null -w "%{http_code}" "https://insidedcpulse.com/grafana/public-dashboards/<token>?kiosk"`
  → `200` for each token, after the setup script has run.

## Out of Scope

- Anonymous access to the full Grafana org (rejected in favor of
  per-dashboard public links).
- Making AI Consensus Health, System Drift Meter, or Agent Reputation Map
  public.
- Any JS-driven/interactive frontend — `/status` is a static page only.
