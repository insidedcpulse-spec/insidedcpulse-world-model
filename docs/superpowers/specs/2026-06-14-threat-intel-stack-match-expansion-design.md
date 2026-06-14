# Threat-Intel Agent: STACK_MATCHES Expansion Design

## Context

`scripts/agents/threat_intel_agent.py` (threat-intel-agent, 6th persona) pulls
the latest unseen CISA KEV entry into `vulnerability.<id>.*` each hourly run
(`:15`). It already computes `stack_match`/`affected_service` via
`match_stack()` against a hand-maintained `STACK_MATCHES` table
(keywords -> stack_match label -> affected `service.*`/`team.*` id,
first-match-wins, case-insensitive substring scan over
`vendor + product + name + description`).

As of 2026-06-14, the live `vulnerability.*` FIFO (10 entries) has
`stack_match`/`affected_service` empty on all 10 — not a bug, just low
coverage: `STACK_MATCHES` has 7 entries covering only part of the deployed
stack, and CISA KEV skews toward broad enterprise/network/mobile CVEs
(Linux Kernel, Android, Cisco, Ivanti, Chromium, SolarWinds, etc.) that
genuinely fall outside a small FastAPI+Postgres+Redis+nginx stack.

## Goal

Expand `STACK_MATCHES` to cover the full deployed InsideDCPulse stack, so
future KEV entries that mention any component we actually run get linked to
the right `service.*`/`team.*` entity (and, via the existing Graph Memory
Projection `REFERENCES`-edge logic in `graph_projection.py`, into the
knowledge graph) — without changing any schema, engine, or graph code.

## Current deployed stack (source of truth)

- **Infra** (`docker/docker-compose.yml`): `postgres:16-alpine`,
  `redis:7-alpine`, `nginx:1.27-alpine`, `certbot/certbot:v5.6.0`,
  `prom/prometheus:v3.12.0`, `grafana/grafana:13.0.2`, plus the Docker/
  containerd runtime and the host Linux kernel (this sandbox IS the VPS).
- **API** (`backend/Dockerfile` + `backend/requirements.txt`):
  `python:3.12-slim` (Debian base), `fastapi==0.115.6`,
  `uvicorn[standard]==0.34.0`, `starlette`, `asyncpg==0.30.0`,
  `pydantic==2.10.4`, `orjson==3.10.12`, `sse-starlette==1.6.5`, `mcp==1.9.1`.

7 of these 14 components are already covered by the existing
`STACK_MATCHES` (nginx, postgres, redis, grafana, prometheus, certbot,
fastapi/starlette/uvicorn/mcp).

## Change

In `scripts/agents/threat_intel_agent.py`, modify the `STACK_MATCHES` list
(currently lines 36-44):

1. **Extend the existing fastapi-stack row** — add `"pydantic"`, `"orjson"`,
   `"sse-starlette"` to its keyword list (still maps to
   `("fastapi/starlette/mcp", "team.sre")`).
2. **Append 6 new rows**, in this order (first-match-wins, so the new broad
   OS/runtime-level rows go after all specific rows — existing and new):

| keywords | stack_match | affected_service |
|---|---|---|
| `["asyncpg"]` | `"asyncpg==0.30.0"` | `"service.payments_db"` |
| `["docker", "containerd", "runc", "moby"]` | `"docker (container runtime)"` | `"team.sre"` |
| `["alpine linux", "alpine"]` | `"alpine (nginx/redis/postgres base images)"` | `"team.sre"` |
| `["openssl"]` | `"openssl (TLS)"` | `"team.sre"` |
| `["debian"]` | `"debian (python:3.12-slim base)"` | `"team.sre"` |
| `["linux kernel"]` | `"linux kernel (host OS)"` | `"team.sre"` |

Resulting `STACK_MATCHES` order (13 rows total):

1. nginx -> `nginx:1.27-alpine` / `service.checkout`
2. postgres, postgresql -> `postgres:16-alpine` / `service.payments_db`
3. redis -> `redis:7-alpine` / `service.checkout`
4. grafana -> `grafana:13.0.2` / `team.sre`
5. prometheus -> `prometheus:v3.12.0` / `team.sre`
6. certbot, let's encrypt, acme -> `certbot:v5.6.0` / `team.sre`
7. fastapi, starlette, uvicorn, mcp, pydantic, orjson, sse-starlette ->
   `fastapi/starlette/mcp` / `team.sre`
8. asyncpg -> `asyncpg==0.30.0` / `service.payments_db`
9. docker, containerd, runc, moby -> `docker (container runtime)` /
   `team.sre`
10. alpine linux, alpine -> `alpine (nginx/redis/postgres base images)` /
    `team.sre`
11. openssl -> `openssl (TLS)` / `team.sre`
12. debian -> `debian (python:3.12-slim base)` / `team.sre`
13. linux kernel -> `linux kernel (host OS)` / `team.sre`

No other code changes: `match_stack()`, `VULNERABILITY_FIELDS`, world_schema,
graph projection, and all routers/MCP tools are unaffected — `stack_match`/
`affected_service` are existing fields, and the Graph Memory Projection's
`REFERENCES` edge (for `affected_service`/`affected_region`/etc, see
`graph_projection.py` `REFERENCE_FIELDS`) already fires automatically once
`affected_service` is non-empty and parses as `<entity>.<id>`.

## Out of scope

- No automated tests — `scripts/agents/*.py` has no `backend/tests` coverage
  by established project convention (same as `research_agent.py`/
  `ai_research_agent.py`).
- No change to matching algorithm (still substring/first-match heuristic,
  same documented caveat: "not a real CPE/SBOM match").
- No retroactive backfill of the 10 existing `vulnerability.*` entries —
  they keep their current (empty) `stack_match`/`affected_service` until
  evicted by the FIFO; only newly-fetched entries get the expanded matching.

## Verification

Manual: call `match_stack(vendor, product, name, description)` with a sample
matching each new row (e.g. a string containing "Linux Kernel" ->
`("linux kernel (host OS)", "team.sre")`) to confirm the table change is
syntactically correct and ordering doesn't shadow existing rows. No live
KEV re-fetch needed for verification (next cron `:15` run picks up the new
table automatically; whether it produces a non-empty match depends on
that hour's chosen CVE).
