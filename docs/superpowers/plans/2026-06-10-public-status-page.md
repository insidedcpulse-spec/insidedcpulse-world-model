# Public Status Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a public, unauthenticated `/status` page on `insidedcpulse.com` that embeds two existing Grafana dashboards (World Stability Index, Event Flow Timeline) via Grafana's native Public Dashboards feature.

**Architecture:** A static `status.html` served by nginx at `/status` contains two `<iframe>`s pointing at `/grafana/public-dashboards/<token>?kiosk`. Grafana gets `GF_SECURITY_ALLOW_EMBEDDING=true` so it doesn't block the iframes. Per-dashboard public access tokens are generated once via Grafana's HTTP API (idempotent script) and pasted into `status.html`. The root `/` JSON gains a `status` key for discoverability.

**Tech Stack:** nginx 1.27 (static file serving), Grafana (Public Dashboards API, GA in v11+), FastAPI (one-line change), bash + curl (one-time setup script).

---

### Task 1: Enable Grafana embedding + nginx static volume

**Files:**
- Modify: `docker/docker-compose.yml:91-95` (grafana service environment)
- Modify: `docker/docker-compose.yml:56-59` (nginx service volumes)

- [ ] **Step 1: Add `GF_SECURITY_ALLOW_EMBEDDING` to the grafana service**

In `docker/docker-compose.yml`, the grafana service environment block currently reads:

```yaml
    environment:
      GF_SECURITY_ADMIN_USER: admin
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
      GF_SERVER_ROOT_URL: https://insidedcpulse.com/grafana/
      GF_SERVER_SERVE_FROM_SUB_PATH: "true"
```

Change it to:

```yaml
    environment:
      GF_SECURITY_ADMIN_USER: admin
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
      GF_SERVER_ROOT_URL: https://insidedcpulse.com/grafana/
      GF_SERVER_SERVE_FROM_SUB_PATH: "true"
      GF_SECURITY_ALLOW_EMBEDDING: "true"
```

- [ ] **Step 2: Add a static-files volume to the nginx service**

In the same file, the nginx service volumes block currently reads:

```yaml
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
      - certbot_www:/var/www/certbot:ro
```

Change it to:

```yaml
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
      - ./nginx/static:/usr/share/nginx/html/static:ro
      - certbot_www:/var/www/certbot:ro
```

- [ ] **Step 3: Verify the YAML is still valid**

Run:
```bash
cd /root/insidedcpulse-world-model/docker
python3 -c "import yaml; yaml.safe_load(open('docker-compose.yml')); print('valid')"
```
Expected: `valid`

- [ ] **Step 4: Verify both edits landed**

Run:
```bash
grep -n "GF_SECURITY_ALLOW_EMBEDDING\|nginx/static" /root/insidedcpulse-world-model/docker/docker-compose.yml
```
Expected (two lines):
```
      GF_SECURITY_ALLOW_EMBEDDING: "true"
      - ./nginx/static:/usr/share/nginx/html/static:ro
```

- [ ] **Step 5: Commit**

```bash
cd /root/insidedcpulse-world-model
git add docker/docker-compose.yml
git commit -m "feat: enable Grafana embedding and add nginx static volume for /status"
```

---

### Task 2: Create the status.html static page

**Files:**
- Create: `docker/nginx/static/status.html`

- [ ] **Step 1: Create the directory and file**

Create `/root/insidedcpulse-world-model/docker/nginx/static/status.html` with this exact content:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>InsideDCPulse — System Status</title>
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f1117;
      color: #e6e6e6;
      margin: 0;
      padding: 2rem;
    }
    h1 { margin-top: 0; }
    p.lede { color: #a0a0b0; max-width: 60ch; }
    a { color: #6ea8fe; }
    .panel { margin: 1.5rem 0; }
    .panel h2 {
      font-size: 1rem;
      font-weight: 600;
      margin-bottom: 0.5rem;
      color: #c0c0d0;
    }
    iframe {
      width: 100%;
      height: 500px;
      border: 1px solid #2a2d3a;
      border-radius: 8px;
      background: #1a1d29;
    }
  </style>
</head>
<body>
  <h1>InsideDCPulse — System Status</h1>
  <p class="lede">
    Live, read-only view of the InsideDCPulse world model: consensus health,
    accept/reject rates, drift, and event throughput. See
    <a href="/docs">/docs</a> for the API and
    <a href="/ws/world-stream">/ws/world-stream</a> for the real-time event feed.
  </p>

  <div class="panel">
    <h2>World Stability Index</h2>
    <iframe src="/grafana/public-dashboards/__WORLD_STABILITY_TOKEN__?kiosk" loading="lazy"></iframe>
  </div>

  <div class="panel">
    <h2>Event Flow Timeline</h2>
    <iframe src="/grafana/public-dashboards/__EVENT_FLOW_TOKEN__?kiosk" loading="lazy"></iframe>
  </div>
</body>
</html>
```

The two `__WORLD_STABILITY_TOKEN__` / `__EVENT_FLOW_TOKEN__` placeholders are
replaced with real Grafana access tokens in Task 7, after the public
dashboards have been created on the live instance (tokens are generated by
Grafana at creation time and cannot be known ahead of time).

- [ ] **Step 2: Verify the file**

Run:
```bash
grep -c "iframe" /root/insidedcpulse-world-model/docker/nginx/static/status.html
```
Expected: `2`

- [ ] **Step 3: Commit**

```bash
cd /root/insidedcpulse-world-model
git add docker/nginx/static/status.html
git commit -m "feat: add static /status page with Grafana dashboard iframes"
```

---

### Task 3: Add nginx /status location

**Files:**
- Modify: `docker/nginx/conf.d/insidedcpulse.conf.ssl`

- [ ] **Step 1: Insert the /status location block**

In `docker/nginx/conf.d/insidedcpulse.conf.ssl`, find this block:

```nginx
    # Public API
    location / {
        limit_req zone=api_limit burst=40 nodelay;
        proxy_pass http://api:8000;
```

Insert a new `location /status` block immediately **before** the `# Public
API` comment line, so the full section reads:

```nginx
    # Public status page (static, Grafana public dashboards embedded)
    location = /status {
        alias /usr/share/nginx/html/static/status.html;
    }

    # Public API
    location / {
        limit_req zone=api_limit burst=40 nodelay;
        proxy_pass http://api:8000;
```

- [ ] **Step 2: Verify the block landed correctly**

Run:
```bash
grep -A3 "location = /status" /root/insidedcpulse-world-model/docker/nginx/conf.d/insidedcpulse.conf.ssl
```
Expected:
```
    location = /status {
        alias /usr/share/nginx/html/static/status.html;
    }
```

- [ ] **Step 3: Commit**

```bash
cd /root/insidedcpulse-world-model
git add docker/nginx/conf.d/insidedcpulse.conf.ssl
git commit -m "feat: serve /status from nginx"
```

---

### Task 4: Add `status` key to the root endpoint

**Files:**
- Modify: `backend/app/main.py:66-73`
- Modify: `backend/tests/test_mcp_mount.py:14-17`

- [ ] **Step 1: Extend the existing root-route assertion (failing test)**

In `backend/tests/test_mcp_mount.py`, the relevant lines currently read:

```python
            r = client.get("/")
            assert r.status_code == 200
            assert r.json()["name"] == "InsideDCPulse"
```

Change to:

```python
            r = client.get("/")
            assert r.status_code == 200
            assert r.json()["name"] == "InsideDCPulse"
            assert r.json()["status"] == "/status"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd /root/insidedcpulse-world-model/backend
.venv/bin/pytest tests/test_mcp_mount.py -v
```
Expected: `FAILED` with `KeyError: 'status'`

- [ ] **Step 3: Add the `status` key to the root response**

In `backend/app/main.py`, the `root()` function currently reads:

```python
@app.get("/", tags=["meta"])
async def root():
    return {
        "name": "InsideDCPulse",
        "description": "Event-Sourced World Model for Multi-LLM Agents",
        "docs": "/docs",
        "world_stream": "/ws/world-stream",
    }
```

Change to:

```python
@app.get("/", tags=["meta"])
async def root():
    return {
        "name": "InsideDCPulse",
        "description": "Event-Sourced World Model for Multi-LLM Agents",
        "docs": "/docs",
        "world_stream": "/ws/world-stream",
        "status": "/status",
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
cd /root/insidedcpulse-world-model/backend
.venv/bin/pytest tests/test_mcp_mount.py -v
```
Expected: `1 passed`

- [ ] **Step 5: Run the full backend test suite**

Run:
```bash
cd /root/insidedcpulse-world-model/backend
.venv/bin/pytest tests/ -v
```
Expected: all tests pass (26 passed before this task; 26 still, since no new test was added — the existing one was extended).

- [ ] **Step 6: Commit**

```bash
cd /root/insidedcpulse-world-model
git add backend/app/main.py backend/tests/test_mcp_mount.py
git commit -m "feat: advertise /status in root endpoint"
```

---

### Task 5: One-time Grafana public-dashboards setup script

**Files:**
- Create: `docker/grafana/setup-public-dashboards.sh`

- [ ] **Step 1: Create the script**

Create `/root/insidedcpulse-world-model/docker/grafana/setup-public-dashboards.sh` with this exact content:

```bash
#!/bin/bash
# One-time, idempotent setup: enable Grafana Public Dashboards for the
# World Stability Index and Event Flow Timeline dashboards, used by /status.
#
# Usage:
#   GRAFANA_ADMIN_PASSWORD=<password> ./setup-public-dashboards.sh [base_url] [resolve_ip]
#
#   base_url   - Grafana base URL behind the nginx proxy
#                (default: https://insidedcpulse.com/grafana)
#   resolve_ip - optional IP to pin base_url's host to via curl --resolve
#                (useful when running on the VPS itself, e.g. 127.0.0.1)
#
# Prints one line per dashboard: "<uid> <created|already-public> accessToken=<token>"
set -euo pipefail

BASE_URL="${1:-https://insidedcpulse.com/grafana}"
RESOLVE_IP="${2:-}"
AUTH="admin:${GRAFANA_ADMIN_PASSWORD}"

CURL_OPTS=(-s -u "$AUTH")
if [ -n "$RESOLVE_IP" ]; then
  HOST=$(echo "$BASE_URL" | sed -E 's#^https?://([^/]+).*#\1#')
  CURL_OPTS+=(--resolve "${HOST}:443:${RESOLVE_IP}")
fi

for uid in idc-world-stability idc-event-flow-timeline; do
  existing=$(curl "${CURL_OPTS[@]}" "$BASE_URL/api/dashboards/uid/$uid/public-dashboards")
  if echo "$existing" | grep -q '"isEnabled":true'; then
    token=$(echo "$existing" | grep -o '"accessToken":"[^"]*"' | head -1 | cut -d'"' -f4)
    echo "$uid already-public accessToken=$token"
  else
    created=$(curl "${CURL_OPTS[@]}" -X POST "$BASE_URL/api/dashboards/uid/$uid/public-dashboards" \
      -H "Content-Type: application/json" \
      -d '{"isEnabled": true, "share": "public"}')
    token=$(echo "$created" | grep -o '"accessToken":"[^"]*"' | head -1 | cut -d'"' -f4)
    echo "$uid created accessToken=$token"
  fi
done
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x /root/insidedcpulse-world-model/docker/grafana/setup-public-dashboards.sh
```

- [ ] **Step 3: Verify the script syntax**

Run:
```bash
bash -n /root/insidedcpulse-world-model/docker/grafana/setup-public-dashboards.sh
```
Expected: no output (exit 0)

- [ ] **Step 4: Commit**

```bash
cd /root/insidedcpulse-world-model
git add docker/grafana/setup-public-dashboards.sh
git commit -m "feat: add one-time Grafana public-dashboards setup script"
```

---

### Task 6: README documentation

**Files:**
- Modify: `README.md:38-53` (Endpoints table)
- Modify: `README.md:95-104` (Observability section)

- [ ] **Step 1: Add `/status` to the Endpoints table**

In `README.md`, the Endpoints table currently ends with:

```markdown
| GET | `/healthz` | Health check |
| GET | `/metrics` | Prometheus metrics |
```

Change to:

```markdown
| GET | `/healthz` | Health check |
| GET | `/metrics` | Prometheus metrics |
| GET | `/status` | Public status page (no auth) — embeds the World Stability Index and Event Flow Timeline Grafana dashboards |
```

- [ ] **Step 2: Note the public dashboards in the Observability section**

In `README.md`, the Observability section currently ends with:

```markdown
- **Event Flow Timeline** — events/sec, API latency p95, Postgres write latency p95, queue size
```

Change to:

```markdown
- **Event Flow Timeline** — events/sec, API latency p95, Postgres write latency p95, queue size

**World Stability Index** and **Event Flow Timeline** are also published
read-only, without login, at [`/status`](https://insidedcpulse.com/status)
via Grafana's [Public Dashboards](https://grafana.com/docs/grafana/latest/dashboards/dashboard-public/)
feature. The other three dashboards remain login-protected under
`/grafana/`. To (re)provision the public links — e.g. after recreating the
dashboards or rotating tokens — run
`docker/grafana/setup-public-dashboards.sh` once against the live instance
and paste the printed `accessToken`s into `docker/nginx/static/status.html`.
```

- [ ] **Step 3: Verify both edits landed**

Run:
```bash
grep -n "/status" /root/insidedcpulse-world-model/README.md
```
Expected: at least 2 matches (the table row and the Observability paragraph).

- [ ] **Step 4: Commit**

```bash
cd /root/insidedcpulse-world-model
git add README.md
git commit -m "docs: document the public /status page"
```

---

### Task 7: Deploy, provision public dashboards, and verify live

**Context:** Tasks 1-6 are committed locally on `main` but not yet pushed.
This task pushes them (triggering the existing webhook auto-deploy on the
VPS), then runs the one-time setup script against the now-live Grafana
instance, fills in the real access tokens in `status.html`, pushes again,
and verifies `/status` end-to-end.

**Files:**
- Modify: `docker/nginx/static/status.html` (replace placeholder tokens)

- [ ] **Step 1: Push Tasks 1-6 to trigger deploy**

The repo is `https://github.com/insidedcpulse-spec/insidedcpulse-world-model`.
GitHub fine-grained PATs for this account get invalidated quickly — ask the
user for a fresh PAT (Contents R&W) immediately before this step. Verify it
first:

```bash
curl -s -H "Authorization: Bearer $TOK" https://api.github.com/user
```
Expected: JSON with `"login":"insidedcpulse-spec"`.

Then push immediately:
```bash
cd /root/insidedcpulse-world-model
git push https://insidedcpulse-spec:$TOK@github.com/insidedcpulse-spec/insidedcpulse-world-model.git main
```
Expected: push succeeds (no `401`).

- [ ] **Step 2: Wait for the webhook deploy and verify the VPS is on the new commit**

Run (waits up to ~2 minutes for the deploy):
```bash
LOCAL_SHA=$(git -C /root/insidedcpulse-world-model rev-parse HEAD)
for i in $(seq 1 24); do
  REMOTE_SHA=$(ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 \
    "git -C /opt/insidedcpulse-world-model rev-parse HEAD")
  [ "$REMOTE_SHA" = "$LOCAL_SHA" ] && echo "deployed $REMOTE_SHA" && break
  sleep 5
done
```
Expected: `deployed <LOCAL_SHA>` (matches the local HEAD from Step 1).

- [ ] **Step 3: Confirm the new containers are healthy**

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 \
  "cd /opt/insidedcpulse-world-model/docker && docker compose ps"
```
Expected: all services `Up` (api, postgres, redis healthy; nginx, grafana running).

- [ ] **Step 4: Run the public-dashboards setup script on the VPS**

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 \
  "cd /opt/insidedcpulse-world-model && \
   GRAFANA_ADMIN_PASSWORD=\$(grep GRAFANA_ADMIN_PASSWORD docker/.env | cut -d= -f2) \
   bash docker/grafana/setup-public-dashboards.sh https://insidedcpulse.com/grafana 127.0.0.1"
```
Expected: two lines, e.g.:
```
idc-world-stability created accessToken=<TOKEN_A>
idc-event-flow-timeline created accessToken=<TOKEN_B>
```
(or `already-public` if re-run). Note `<TOKEN_A>` and `<TOKEN_B>`.

- [ ] **Step 5: Replace the placeholder tokens in status.html**

```bash
cd /root/insidedcpulse-world-model
sed -i "s/__WORLD_STABILITY_TOKEN__/<TOKEN_A>/" docker/nginx/static/status.html
sed -i "s/__EVENT_FLOW_TOKEN__/<TOKEN_B>/" docker/nginx/static/status.html
```
(substitute the real tokens from Step 4 for `<TOKEN_A>` / `<TOKEN_B>`)

Verify no placeholders remain:
```bash
grep -c "__.*_TOKEN__" docker/nginx/static/status.html
```
Expected: `0`

- [ ] **Step 6: Commit and push the real tokens**

```bash
cd /root/insidedcpulse-world-model
git add docker/nginx/static/status.html
git commit -m "chore: fill in Grafana public-dashboard tokens for /status"
git push https://insidedcpulse-spec:$TOK@github.com/insidedcpulse-spec/insidedcpulse-world-model.git main
```
(reuse `$TOK` from Step 1 if still valid; otherwise repeat the
verify-then-push pattern from Step 1 with a fresh PAT)

- [ ] **Step 7: Wait for the second deploy**

Repeat the polling loop from Step 2 with the new `LOCAL_SHA`.

- [ ] **Step 8: Verify /status live end-to-end**

This sandbox's DNS resolves `insidedcpulse.com` to the wrong IP — pin it
with `--resolve`:

```bash
curl -s --resolve insidedcpulse.com:443:2.25.169.27 -o /dev/null -w "%{http_code}\n" https://insidedcpulse.com/status
curl -s --resolve insidedcpulse.com:443:2.25.169.27 https://insidedcpulse.com/status | grep -c iframe
curl -s --resolve insidedcpulse.com:443:2.25.169.27 https://insidedcpulse.com/ | grep -o '"status":"[^"]*"'
```
Expected:
```
200
2
"status":"/status"
```

- [ ] **Step 9: Verify each public dashboard responds**

```bash
curl -s --resolve insidedcpulse.com:443:2.25.169.27 -o /dev/null -w "%{http_code}\n" \
  "https://insidedcpulse.com/grafana/public-dashboards/<TOKEN_A>?kiosk"
curl -s --resolve insidedcpulse.com:443:2.25.169.27 -o /dev/null -w "%{http_code}\n" \
  "https://insidedcpulse.com/grafana/public-dashboards/<TOKEN_B>?kiosk"
```
Expected: `200` for both (substitute the real tokens).

---

## Self-Review Notes

- **Spec coverage:** all 6 spec components covered — Task 1 (allow_embedding +
  volume), Task 2 (status.html), Task 3 (nginx location), Task 4 (root key +
  test), Task 5 (setup script), Task 6 (README), Task 7 (deploy + token
  provisioning + live verification, matching the spec's "Data Flow" and
  "Testing" sections exactly).
- **Placeholders:** `__WORLD_STABILITY_TOKEN__` / `__EVENT_FLOW_TOKEN__` in
  Task 2 and `<TOKEN_A>` / `<TOKEN_B>` in Task 7 are intentional — they hold
  values Grafana generates at runtime (per spec, "tokens cannot be known
  ahead of time"), and Task 7 explicitly defines how they're produced and
  substituted.
- **Type/path consistency:** dashboard UIDs (`idc-world-stability`,
  `idc-event-flow-timeline`) match the provisioned JSON files; nginx alias
  path (`/usr/share/nginx/html/static/status.html`) matches the volume mount
  target in Task 1; `status` key name matches between Task 4's main.py edit,
  its test, and Task 6's README/Task 2's html links.
