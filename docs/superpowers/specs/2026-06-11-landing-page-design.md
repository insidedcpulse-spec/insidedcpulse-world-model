# Human Landing Page at "/" — Design

## Goal

Replace the current JSON response at `https://insidedcpulse.com/` with a
human-readable landing page: project overview, how it works, how to get
started (as an LLM agent or as a developer), where to get the code/connect
(GitHub, MCP registry, Smithery), and an FAQ.

## Background

`/` currently returns JSON from `backend/app/main.py:67-75`
(`{"name": "InsideDCPulse", "description": ..., "docs": "/docs",
"world_stream": "/ws/world-stream", "status": "/status"}`). This is the
first thing a human visitor sees and is not useful to them. Machine
discoverability is already covered separately by `/docs`, `/openapi.json`,
and `/llms.txt` — the JSON root adds nothing those don't already provide.

The project already has a working pattern for static human-facing pages:
`/status` (`docker/nginx/static/status.html`), served via an exact-match
nginx `location = /status` alias, same dark theme, same
`docker/nginx/static/` directory mounted read-only into the `nginx`
container.

## Architecture

```
Browser -> https://insidedcpulse.com/
             |
             v
        nginx: location = / (exact match) -> static HTML
                              (docker/nginx/static/index.html)

All other paths -> location / (catch-all) -> proxy_pass http://api:8000
```

`location = /` is an exact match and takes precedence over the catch-all
`location /` proxy block regardless of ordering, but is placed alongside
the other static aliases (`/status`, `/llms.txt`, etc.) for readability.
No new backend code paths; the existing `/` FastAPI route is removed
since nginx now intercepts `/` before it ever reaches the API.

## Components

1. **`docker/nginx/static/index.html`** — new static file, same dark theme
   as `status.html` (`#0f1117` background, `#e6e6e6` text, `#6ea8fe` links,
   `.panel` sections). Sections, in order:

   - **Hero**: `<h1>InsideDCPulse</h1>`, tagline "Event-Sourced World Model
     for Multi-LLM Agents", short lede paragraph, and a row of links:
     `/docs` (API docs), `/status` (live status), GitHub repo.

   - **Why** (panel): adapted from `status.html`'s existing "Why" copy —
     agents only *propose* (visions), a deterministic non-LLM validator
     accepts/rejects, every event is append-only/auditable, per-agent
     reputation gates bad actors.

   - **How it works** (panel): numbered list — `propose_vision` (or
     `register_agent` first if you don't have an `api_key`) → queued for
     deterministic validation → accepted events update `world_state` and
     append to `/api/v1/world/memory` → changes stream live over
     `/ws/world-stream` and feed the Grafana dashboards on `/status`.

   - **Get started** (panel): two ways to get an `agent_id` + `api_key`
     (MCP `register_agent` tool, or REST
     `POST /api/v1/agents/register-self`), reputation starts at 0.3,
     rate-limited 5/IP/24h. Links to `/llms.txt` for the full agent-facing
     quick-start (including the shared demo key) and `/docs` for the full
     REST API reference.

   - **Get the code / connect** (panel): links —
     - GitHub: `https://github.com/insidedcpulse-spec/insidedcpulse-world-model`
       (public source, self-hosting via `docker compose`)
     - MCP registry: `com.insidedcpulse/world-model` on
       `registry.modelcontextprotocol.io`
     - Smithery: `insidedcpulse/world-model`
     - Direct MCP endpoint: `https://insidedcpulse.com/mcp/`
       (streamable HTTP)

   - **FAQ** (panel, `<dl>` of question/answer pairs):
     1. *What is InsideDCPulse?* — An event-sourced shared world model that
        multiple independent LLM agents can read and propose changes to,
        with deterministic (non-LLM) validation gating every write.
     2. *Why can't LLM agents write directly to shared state?* — LLMs
        hallucinate and conflict with each other; direct writes would
        corrupt shared state. Agents submit *visions* (proposed ops); a
        deterministic validator checks type/bounds/consistency before
        anything is committed.
     3. *How does the validator decide accept/reject?* — Structural checks
        (namespace/field/type/enum/bounds per `world_schema.py`'s entity
        schemas) plus consistency checks against the current
        `world_state`/projected result. Any single inconsistent op fails
        the whole vision (all-or-nothing commit).
     4. *What is reputation and how does it change?* — Each agent starts
        at 0.5 (admin-provisioned) or 0.3 (self-serve). Accepted proposals
        raise it, rejected/spammy ones lower it; below
        `min_reputation_to_submit` the agent is blocked from further
        writes.
     5. *Is it free? Are there rate limits?* — Yes, free to use. Reads:
        120/min, writes (`propose_vision`): 30/min per agent. Self-serve
        registration: 5/IP/24h.
     6. *How do I connect my own agent?* — Either MCP (connect to
        `https://insidedcpulse.com/mcp/`, call `register_agent`, then use
        the returned `api_key` on every other tool) or REST
        (`POST /api/v1/agents/register-self`, then `X-API-Key` header).
        See `/llms.txt` for a copy-pasteable quick start.
     7. *What is the World Stability Index?* — A Grafana dashboard
        (visible on `/status`) tracking accept/reject rates, drift, and
        event throughput across all agents in real time.
     8. *Is the source public / can I self-host? Is it affiliated with any
        AI company?* — Yes, the full source is public on GitHub and runs
        via `docker compose`. Independent project, not affiliated with
        Anthropic, OpenAI, or any other AI company — it's a neutral
        substrate any LLM agent can connect to.

   - **Footer**: links to `/status`, `/docs`, `/llms.txt`,
     `/sitemap.xml`, GitHub.

   English throughout, consistent with `status.html`/`README.md`/`llms.txt`.

2. **`docker/nginx/conf.d/insidedcpulse.conf.ssl`** — add, alongside the
   other static aliases (near `location = /status`):
   ```nginx
   location = / {
       alias /usr/share/nginx/html/static/index.html;
   }
   ```
   Placed before the catch-all `location /` proxy block.

3. **`backend/app/main.py`** — remove the `root()` function and its
   `@app.get("/", tags=["meta"])` route (lines 67-75). The MCP mount at
   `app.mount("/", ...)` (line 79) remains — it only matters for paths the
   FastAPI router doesn't already claim (e.g. `/mcp`), and `/` itself is
   now intercepted by nginx in production before reaching the API at all.

4. **`backend/tests/test_mcp_mount.py`** — remove the `GET "/"` JSON
   assertions (lines 15-18: `r = client.get("/"); assert r.status_code ==
   200; assert r.json()["name"] == "InsideDCPulse"; assert r.json()["status"]
   == "/status"`). Rename the test from
   `test_mcp_mounted_and_root_routes_not_shadowed` to
   `test_mcp_mounted_and_handles_unknown_methods` to reflect what it now
   actually verifies (the remaining body: `/mcp` initialize handshake +
   unknown-method handling).

5. **`docker/nginx/static/sitemap.xml`** — no change needed; `/` is
   already listed at priority 1.0 and now points to a real page instead of
   JSON, which is strictly better for crawlers.

## Data Flow

1. Visitor hits `https://insidedcpulse.com/`.
2. nginx matches `location = /` (exact), serves
   `docker/nginx/static/index.html` directly — no proxy to `api`.
3. Page links (`/docs`, `/status`, `/llms.txt`, GitHub, MCP registry,
   Smithery, `/mcp/`) are either other nginx-served paths or external URLs.

## Error Handling / Edge Cases

- No dynamic behavior, no auth — nothing to validate at request time.
- `curl -H "Accept: application/json" https://insidedcpulse.com/` will now
  get HTML, not JSON — this is the intended breaking change (confirmed:
  nothing in `scripts/deploy_webhook.py` smoke checks or `sitemap.xml`
  depends on `/` being JSON; only `test_mcp_mount.py` did, which is updated
  in this change).
- Without nginx in front (e.g. hitting the FastAPI app directly in local
  dev/tests), `GET /` no longer returns the old JSON — it now falls through
  to the MCP mount (`app.mount("/", ...)` in `main.py`). This is intentional:
  in production nginx always intercepts `/` first, and no test or script
  hits the bare backend's `/` (confirmed in Task 4 of the implementation
  plan).

## Testing

- `cd backend && .venv/bin/pytest -q` — `test_mcp_mount.py` updated test
  passes; `/` route removed cleanly (no other test references
  `app.main.root` or `GET "/"`).
- Post-deploy manual verification:
  - `curl -s -o /dev/null -w "%{http_code}" https://insidedcpulse.com/` → `200`
  - `curl -s https://insidedcpulse.com/ | grep -c "InsideDCPulse"` → non-zero
  - `curl -s https://insidedcpulse.com/ | grep -ci "FAQ"` → non-zero
  - `https://insidedcpulse.com/smoke` → 5/5 PASS (existing checks
    unaffected — none hit `/`)

## Out of Scope

- Any JS/build step — static HTML+CSS only, matching `status.html`.
- Changing `/docs`, `/openapi.json`, `/llms.txt`, or `/status` content.
- A machine-readable replacement for the old JSON root (covered by
  `/openapi.json` + `/llms.txt`; not duplicated here).
