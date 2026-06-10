# Webhook Auto-Deploy (GitHub Actions billing-lock workaround)

## Context

GitHub account `insidedcpulse-spec` is billing-locked, so the existing
`.github/workflows/deploy.yml` (Actions-based SSH deploy) cannot run
(`run cannot be rerun; its workflow file may be broken` — the standard
Actions error when an account is billing-locked). The site is currently
deployed manually via SSH. This design adds a GitHub webhook -> small
HTTP listener on the VPS that performs the same deploy steps, with no
dependency on GitHub Actions.

## Architecture

```
GitHub push (branch main)
  -> POST https://insidedcpulse.com/hooks/deploy   (X-Hub-Signature-256: sha256=...)
  -> nginx (docker container, internal network)
  -> http://host.docker.internal:9001/hooks/deploy  (extra_hosts: host-gateway)
  -> systemd service "insidedcpulse-webhook" (host, root)
       - verify HMAC-SHA256(secret, raw_body) against X-Hub-Signature-256
       - verify X-GitHub-Event == "push" and JSON ref == "refs/heads/main"
       - respond 200 immediately
       - run deploy in a background thread:
           cd /opt/insidedcpulse-world-model
           git fetch origin main
           git reset --hard origin/main
           cd docker
           docker compose build api
           docker compose up -d --remove-orphans
           docker image prune -f
```

This mirrors the steps in `.github/workflows/deploy.yml` exactly, just
triggered via an HTTP webhook instead of an Actions runner.

## Components

### 1. `scripts/deploy_webhook.py` (tracked in repo)

- Python 3 stdlib only (`http.server`, `hmac`, `hashlib`, `subprocess`,
  `threading`, `json`, `os`) — no extra dependencies, no venv needed.
- Listens on `127.0.0.1:9001`.
- Reads `WEBHOOK_SECRET` from environment (provided by systemd
  `EnvironmentFile`).
- `POST /hooks/deploy`:
  - Read raw request body.
  - Compute `hmac.new(secret, body, hashlib.sha256).hexdigest()`,
    compare with `X-Hub-Signature-256` header (`sha256=<hex>`) using
    `hmac.compare_digest`. Mismatch or missing header -> `401`, log
    warning, no deploy.
  - If `X-GitHub-Event` header != `push` -> `200` ack, no deploy.
  - Parse JSON body, check `ref == "refs/heads/main"`. If not -> `200`
    ack, log info, no deploy.
  - Otherwise: respond `200` immediately, then in a background thread
    run the deploy command sequence (above) via `subprocess.run`,
    `cwd=/opt/insidedcpulse-world-model` / `.../docker`, streaming
    stdout/stderr to the process's own stdout (captured by journald).
  - Any other path/method -> `404`.
- Any other endpoint (e.g. `GET /healthz`) -> simple `200 ok` for
  manual liveness checks.

### 2. systemd unit `insidedcpulse-webhook.service` (host-only, NOT
tracked in repo — host-specific, created directly on the VPS)

```ini
[Unit]
Description=InsideDCPulse deploy webhook
After=network.target docker.service

[Service]
Type=simple
User=root
EnvironmentFile=/root/insidedcpulse-secrets/webhook.env
ExecStart=/usr/bin/python3 /opt/insidedcpulse-world-model/scripts/deploy_webhook.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

### 3. nginx changes (tracked)

- `docker/docker-compose.yml`: add to the `nginx` service:
  ```yaml
  extra_hosts:
    - "host.docker.internal:host-gateway"
  ```
- `docker/nginx/conf.d/insidedcpulse.conf.ssl`: add, inside the `:443`
  server block, before the catch-all `location /`:
  ```nginx
  location /hooks/deploy {
      proxy_pass http://host.docker.internal:9001/hooks/deploy;
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-GitHub-Event $http_x_github_event;
      proxy_set_header X-Hub-Signature-256 $http_x_hub_signature_256;
  }
  ```
  (default nginx behaviour already forwards arbitrary headers via
  `proxy_pass`, the explicit lines are for clarity/safety in case a
  shared `proxy_set_header` block elsewhere overrides them.)
- The bootstrap config (`insidedcpulse.conf.bootstrap`) is not changed
  — it's only used before SSL exists and is not the live config.

### 4. Secret + GitHub webhook registration (one-time, manual/CLI, not
tracked)

- Generate: `openssl rand -hex 32` -> `WEBHOOK_SECRET`.
- Store in `/root/insidedcpulse-secrets/webhook.env` on both the VPS
  and the local secrets copy:
  ```
  WEBHOOK_SECRET=<hex>
  ```
- Register via:
  ```
  gh api repos/insidedcpulse-spec/insidedcpulse-world-model/hooks \
    -f name=web -f config[url]=https://insidedcpulse.com/hooks/deploy \
    -f config[content_type]=json -f config[secret]=<hex> \
    -f config[insecure_ssl]=0 -f events[]=push -F active=true
  ```

## Error handling

| Condition | Response | Action |
|---|---|---|
| Missing/invalid `X-Hub-Signature-256` | `401` | log warning, no deploy |
| `X-GitHub-Event != push` | `200` | log info, no deploy (ack) |
| `ref != refs/heads/main` | `200` | log info, no deploy (ack) |
| `git`/`docker compose` step fails (non-zero exit) | n/a (already responded 200) | log error to journal; no auto-retry — next push to main retries the full sequence |
| Any other path | `404` | — |

No retry/queue logic — deploys are idempotent (`git reset --hard` +
`docker compose up -d`), so a failed run is fixed by the next push or
a manual re-trigger (re-deliver webhook from GitHub UI, or `gh api
repos/.../hooks/<id>/tests`).

## Testing

1. `gh api repos/.../hooks/<id>/tests` (ping/test delivery) -> expect
   `200` from the listener (ack path, no `push` event).
2. `systemctl status insidedcpulse-webhook` + `journalctl -u
   insidedcpulse-webhook -f` while making a trivial commit + push to
   `main` -> confirm signature verified, deploy sequence runs,
   `docker compose ps` shows `api` rebuilt (new container ID /
   `CREATED` timestamp).
3. Negative test: `curl -X POST https://insidedcpulse.com/hooks/deploy
   -d '{}'` (no/garbage signature) -> expect `401`, no deploy
   triggered (verify via journal — no git/docker activity logged).

## Out of scope

- Does not touch `.github/workflows/deploy.yml` (left as-is; can be
  re-enabled once billing is fixed — both mechanisms are harmless to
  run side by side since deploys are idempotent).
- Does not rebuild/restart `nginx`, `grafana`, `prometheus`, etc. —
  only `api`, matching current Actions workflow scope.
