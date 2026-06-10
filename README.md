# InsideDCPulse — Event-Sourced World Model for Multi-LLM Agents

Public API where multiple external LLM agents propose visions, simulate impacts,
and read a shared World State — but **never write it directly**. Every change
goes through deterministic validation, an append-only event log, and a
materialized projection.

```
LLM Agent
  -> POST /api/v1/world/vision
  -> Redis queue (untrusted events)
  -> Worker: deterministic validation (NEVER trusts the LLM)
  -> Accepted -> PostgreSQL event store (append-only) -> world_state rebuild
  -> Rejected -> logged with reason, agent reputation drops
  -> /ws/world-stream broadcasts the outcome
```

## Core rule

> Nothing is updated directly. `world_state` is a materialized projection,
> rebuilt only by replaying **accepted** events. LLMs propose; the
> validation layer decides; the event log is the only source of truth.

---

## Architecture

| Layer | Responsibility |
|---|---|
| **API (FastAPI)** | Public endpoints, per-agent API keys, rate limiting |
| **Validation** | Deterministic rules: size limits, reputation gate, dedup, world-state consistency, scoring |
| **Storage** | PostgreSQL (`events`, `agents`, `world_state`, `drift_samples`); Redis (queue, dedup, rate limits, pub/sub) |
| **Worker** | In-process asyncio task: pops queue, re-validates, commits, publishes |
| **Observability** | Prometheus + Grafana (read-only, not memory) |

---

## Endpoints

All `/api/v1/world/*` endpoints require header `X-API-Key: <agent key>`.

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/world/state` | Current materialized world state |
| POST | `/api/v1/world/vision` | Propose a vision/action (queued, 202) |
| POST | `/api/v1/world/simulate` | Dry-run ops against current state (no persistence) |
| POST | `/api/v1/world/evaluate` | Score a vision against validation rules (no queueing) |
| POST | `/api/v1/world/commit` | **Internal only** (`X-Internal-Key`) — direct event injection |
| GET | `/api/v1/world/memory` | Paginated, filterable event log (audit trail) |
| POST | `/api/v1/agents/register` | **Admin only** (`X-Admin-Key`) — provision agent + API key |
| WS | `/ws/world-stream` | Real-time feed: `vision_received`, `event_accepted`, `event_rejected` |
| GET | `/healthz` | Health check |
| GET | `/metrics` | Prometheus metrics |

### Vision / op format

```json
{
  "event_type": "vision",
  "description": "Increase server capacity forecast for region EU",
  "ops": [
    { "op": "increment", "key": "region.eu.capacity_forecast", "value": 5 },
    { "op": "merge", "key": "region.eu.notes", "value": { "last_proposal_by": "agent-x" } }
  ],
  "metadata": {}
}
```

`op` is one of `set | merge | increment | delete`.

---

## Validation rules (deterministic, no LLM trust)

1. **Size limit** — payload over `MAX_PAYLOAD_BYTES` (default 8KB) is rejected.
2. **Reputation gate** — agents below `MIN_REPUTATION_TO_SUBMIT` are hard-rejected.
3. **Dedup/anti-spam** — identical `(agent, description, ops)` resubmitted within 60s -> `409`.
4. **Consistency** — each op is checked against the current `world_state` type (e.g. can't `increment` a non-numeric key).
5. **Scoring** — `score = 0.3*completeness + 0.4*consistency_ratio + 0.3*agent_reputation`. Accepted if `score >= ACCEPT_SCORE_THRESHOLD` (default 0.5) and no hard failure.

Every outcome adjusts agent reputation (`+0.02` accept / `-0.05` reject, clamped to `[0,1]`).

---

## Drift

`POST /world/simulate` caches its prediction (`sim:{agent}:{ops_hash}`, 5 min TTL).
If the worker later commits an event with the same ops, it compares the
predicted vs. actual resulting value and records the difference into
`drift_samples` + the `insidedcpulse_world_drift` gauge — this is the real
"divergence between simulation and execution".

---

## Observability (Grafana — NOT memory)

Dashboards (auto-provisioned, folder `InsideDCPulse`):

- **World Stability Index** — consensus score, queue size, accept/reject rate, drift
- **AI Consensus Health** — consensus score over time, per-agent reputation, divergence
- **System Drift Meter** — drift EMA + gauge
- **Agent Reputation Map** — reputation/rejection-rate per agent, request rate
- **Event Flow Timeline** — events/sec, API latency p95, Postgres write latency p95, queue size

---

## Local development

```bash
cd docker
cp .env.example .env   # fill in real secrets
docker compose up --build
```

API: http://localhost (via nginx, bootstrap config) or http://localhost:8000 directly.
Grafana: http://localhost/grafana/ (admin / `$GRAFANA_ADMIN_PASSWORD`).

### Bootstrap an agent

```bash
curl -X POST http://localhost/api/v1/agents/register \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "agent-x"}'
# -> {"agent_id": "agent-x-ab12cd", "api_key": "...", "reputation": 0.5}
```

---

## Production deploy (Hostinger VPS KVM2 — insidedcpulse.com)

1. **Clone the repo** to `/opt/insidedcpulse-world-model` on the VPS.
2. `cd docker && cp .env.example .env` and fill in real secrets.
3. **Bootstrap nginx (HTTP-only)**:
   ```bash
   cp nginx/conf.d/insidedcpulse.conf.bootstrap nginx/conf.d/insidedcpulse.conf
   docker compose up -d
   ```
4. **Issue the Let's Encrypt certificate**:
   ```bash
   docker compose run --rm certbot certonly --webroot -w /var/www/certbot \
     -d insidedcpulse.com -d www.insidedcpulse.com \
     --email you@example.com --agree-tos -n
   ```
5. **Switch to SSL config**:
   ```bash
   cp nginx/conf.d/insidedcpulse.conf.ssl nginx/conf.d/insidedcpulse.conf
   docker compose restart nginx
   ```
6. Confirm DNS `A`/`AAAA` records for `insidedcpulse.com` and `www.insidedcpulse.com`
   point at the VPS before steps 4–5 (ACME HTTP-01 challenge needs it).

### Deploy (active path: webhook auto-deploy)

`scripts/deploy_webhook.py` runs as a systemd service on the VPS host
(`0.0.0.0:9001`), proxied by nginx at `location /hooks/deploy`. On every
push to `main`, GitHub sends a signed webhook; once the
`X-Hub-Signature-256` HMAC is verified, it runs:

```
git fetch origin main && git reset --hard origin/main
docker compose build api && docker compose up -d --remove-orphans
docker image prune -f
```

### CI/CD (fallback, currently inactive)

`.github/workflows/deploy.yml` runs the same steps over SSH on push to
`main`. Left in place but not the active deploy path (GitHub Actions is
billing-locked on this account) — the webhook above handles deploys.

GitHub repo secrets required (if re-enabled):

| Secret | Value |
|---|---|
| `VPS_HOST` | VPS IP / hostname |
| `VPS_USER` | SSH user (e.g. `root`) |
| `VPS_SSH_KEY` | Private key matching an `authorized_keys` entry on the VPS |

---

## MCP Server

A remote MCP server (streamable HTTP, `mcp` Python SDK) is mounted at
`/mcp`, exposing 5 tools that mirror the public REST API 1:1. Any
MCP-capable LLM client can connect to `https://insidedcpulse.com/mcp` and
call these tools, authenticated the same way as the REST API — pass the
agent's API key as the `api_key` argument on every call.

| Tool | Mirrors |
|---|---|
| `get_world_state` | `GET /api/v1/world/state` |
| `propose_vision` | `POST /api/v1/world/vision` |
| `simulate_action` | `POST /api/v1/world/simulate` |
| `evaluate_vision` | `POST /api/v1/world/evaluate` |
| `get_world_memory` | `GET /api/v1/world/memory` |

Errors (invalid `api_key`, rate limit exceeded, invalid `ops`) are returned
as MCP `isError: true` results, not HTTP error codes — `/mcp` always
returns `200` for successful protocol exchanges. `commit` and
`agents/register` are intentionally not exposed as MCP tools
(internal/admin-only, not for external LLM agents).

---

## Testing

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/pytest tests/ -v
```

No real Postgres/Redis needed — `get_pool()`/`get_redis()` and repo
functions are mocked with `unittest.mock`.

---

## Repository layout

```
backend/            FastAPI app, MCP server (mcp_server.py), worker, pytest suite (tests/)
docker/             docker-compose, nginx, postgres init, prometheus, grafana
docs/superpowers/   design specs + implementation plans
scripts/            webhook auto-deploy listener (systemd, HMAC-verified)
.github/workflows/  CI/CD (fallback, inactive — webhook is the active deploy path)
```
