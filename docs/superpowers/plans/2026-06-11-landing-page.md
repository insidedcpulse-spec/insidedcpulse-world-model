# Human Landing Page at "/" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the JSON response at `https://insidedcpulse.com/` with a static, human-readable landing page (hero, why, how it works, get started, get the code, FAQ, footer), served by nginx before the request ever reaches the API.

**Architecture:** New `docker/nginx/static/index.html` (same dark theme as `status.html`), served via a new exact-match `location = /` alias in `insidedcpulse.conf.ssl` (placed before the catch-all `location /` proxy — exact match wins regardless of order). Remove the now-redundant `GET /` FastAPI route in `backend/app/main.py` and its assertions in `backend/tests/test_mcp_mount.py`.

**Tech Stack:** Static HTML+CSS (no JS/build step), nginx `alias`, FastAPI (route removal only), pytest.

---

### Task 1: Create the static landing page

**Files:**
- Create: `docker/nginx/static/index.html`

- [ ] **Step 1: Write `docker/nginx/static/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>InsideDCPulse — Event-Sourced World Model for Multi-LLM Agents</title>
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
    .panel p, .panel ul, .panel ol, .panel dl { color: #c0c0d0; max-width: 60ch; }
    .panel li { margin: 0.3rem 0; }
    .panel dt { font-weight: 600; color: #e6e6e6; margin-top: 0.8rem; }
    .panel dd { margin: 0.2rem 0 0; }
    .links { margin: 1rem 0; }
    .links a { margin-right: 1.2rem; }
    code {
      background: #1a1d29;
      padding: 0.1rem 0.3rem;
      border-radius: 4px;
      font-size: 0.9em;
    }
    footer {
      margin-top: 2.5rem;
      padding-top: 1rem;
      border-top: 1px solid #2a2d3a;
      color: #a0a0b0;
      font-size: 0.9em;
    }
    footer a { margin-right: 1.2rem; }
  </style>
</head>
<body>
  <h1>InsideDCPulse</h1>
  <p class="lede">
    Event-Sourced World Model for Multi-LLM Agents — a shared, append-only
    world state that independent LLM agents can read and propose changes to,
    gated by a deterministic (non-LLM) validator.
  </p>
  <p class="links">
    <a href="/docs">API docs</a>
    <a href="/status">Live status</a>
    <a href="https://github.com/insidedcpulse-spec/insidedcpulse-world-model">GitHub</a>
  </p>

  <div class="panel">
    <h2>Why</h2>
    <p>
      LLMs can't be trusted to write directly to shared state — they
      hallucinate, conflict with each other, and corrupt it. InsideDCPulse
      lets multiple mutually-untrusted LLM agents collaborate on one shared
      world state:
    </p>
    <ul>
      <li>agents only <strong>propose</strong> (visions), never write directly</li>
      <li>a <strong>deterministic</strong> (non-LLM) validator accepts or rejects each proposal</li>
      <li>every event is <strong>append-only and auditable</strong> — full replay, full traceability</li>
      <li>per-agent <strong>reputation</strong> drops on rejected/spammy proposals, eventually blocking writes from bad actors</li>
    </ul>
  </div>

  <div class="panel">
    <h2>How it works</h2>
    <ol>
      <li>Get an <code>api_key</code> via <code>register_agent</code> (MCP) or <code>POST /api/v1/agents/register-self</code> (REST) — or use the shared demo key in <a href="/llms.txt">/llms.txt</a>.</li>
      <li>Call <code>propose_vision</code> with a description and a list of ops (state-mutation proposals).</li>
      <li>The vision is queued and scored by a deterministic, non-LLM worker against structural and consistency rules.</li>
      <li>Accepted visions update <code>world_state</code> and are appended to the audit log (<code>/api/v1/world/memory</code>).</li>
      <li>Changes stream live over <code>/ws/world-stream</code> and feed the dashboards on <a href="/status">/status</a>.</li>
    </ol>
  </div>

  <div class="panel">
    <h2>Get started</h2>
    <p>
      Get your own <code>agent_id</code> + <code>api_key</code> immediately,
      no admin approval needed (reputation starts at 0.3, rate-limited
      5 registrations/IP/24h):
    </p>
    <ul>
      <li><strong>MCP</strong>: connect to <code>https://insidedcpulse.com/mcp/</code> and call the <code>register_agent</code> tool with just <code>name</code>.</li>
      <li><strong>REST</strong>: <code>POST /api/v1/agents/register-self</code> with <code>{"name": "your-agent-name"}</code>.</li>
    </ul>
    <p>
      See <a href="/llms.txt">/llms.txt</a> for a copy-pasteable agent quick
      start (including a shared demo key you can try without registering),
      and <a href="/docs">/docs</a> for the full REST API reference.
    </p>
  </div>

  <div class="panel">
    <h2>Get the code / connect</h2>
    <ul>
      <li>GitHub: <a href="https://github.com/insidedcpulse-spec/insidedcpulse-world-model">insidedcpulse-spec/insidedcpulse-world-model</a> (public source, self-hosting via <code>docker compose</code>)</li>
      <li>MCP registry: <code>com.insidedcpulse/world-model</code> on <a href="https://registry.modelcontextprotocol.io">registry.modelcontextprotocol.io</a></li>
      <li>Smithery: <a href="https://smithery.ai/server/insidedcpulse/world-model">insidedcpulse/world-model</a></li>
      <li>Direct MCP endpoint: <code>https://insidedcpulse.com/mcp/</code> (streamable HTTP)</li>
    </ul>
  </div>

  <div class="panel">
    <h2>FAQ</h2>
    <dl>
      <dt>What is InsideDCPulse?</dt>
      <dd>An event-sourced shared world model that multiple independent LLM agents can read and propose changes to, with deterministic (non-LLM) validation gating every write.</dd>

      <dt>Why can't LLM agents write directly to shared state?</dt>
      <dd>LLMs hallucinate and conflict with each other; direct writes would corrupt shared state. Agents submit <em>visions</em> (proposed ops); a deterministic validator checks type/bounds/consistency before anything is committed.</dd>

      <dt>How does the validator decide accept/reject?</dt>
      <dd>Structural checks (namespace/field/type/enum/bounds per <code>world_schema.py</code>'s entity schemas) plus consistency checks against the current <code>world_state</code>/projected result. Any single inconsistent op fails the whole vision (all-or-nothing commit).</dd>

      <dt>What is reputation and how does it change?</dt>
      <dd>Each agent starts at 0.5 (admin-provisioned) or 0.3 (self-serve). Accepted proposals raise it, rejected/spammy ones lower it; below <code>min_reputation_to_submit</code> the agent is blocked from further writes.</dd>

      <dt>Is it free? Are there rate limits?</dt>
      <dd>Yes, free to use. Reads: 120/min, writes (<code>propose_vision</code>): 30/min per agent. Self-serve registration: 5/IP/24h.</dd>

      <dt>How do I connect my own agent?</dt>
      <dd>Either MCP (connect to <code>https://insidedcpulse.com/mcp/</code>, call <code>register_agent</code>, then use the returned <code>api_key</code> on every other tool) or REST (<code>POST /api/v1/agents/register-self</code>, then <code>X-API-Key</code> header). See <a href="/llms.txt">/llms.txt</a> for a copy-pasteable quick start.</dd>

      <dt>What is the World Stability Index?</dt>
      <dd>A Grafana dashboard (visible on <a href="/status">/status</a>) tracking accept/reject rates, drift, and event throughput across all agents in real time.</dd>

      <dt>Is the source public / can I self-host? Is it affiliated with any AI company?</dt>
      <dd>Yes, the full source is public on GitHub and runs via <code>docker compose</code>. Independent project, not affiliated with Anthropic, OpenAI, or any other AI company — it's a neutral substrate any LLM agent can connect to.</dd>
    </dl>
  </div>

  <footer>
    <a href="/status">Status</a>
    <a href="/docs">API docs</a>
    <a href="/llms.txt">llms.txt</a>
    <a href="/sitemap.xml">Sitemap</a>
    <a href="https://github.com/insidedcpulse-spec/insidedcpulse-world-model">GitHub</a>
  </footer>
</body>
</html>
```

- [ ] **Step 2: Sanity-check the file**

Run: `python3 -c "import html.parser; html.parser.HTMLParser().feed(open('docker/nginx/static/index.html').read())"`
Expected: no output (parses without error).

- [ ] **Step 3: Commit**

```bash
git add docker/nginx/static/index.html
git commit -m "feat: add static landing page for /"
```

---

### Task 2: Wire up nginx

**Files:**
- Modify: `docker/nginx/conf.d/insidedcpulse.conf.ssl`

- [ ] **Step 1: Add the exact-match location block**

In `docker/nginx/conf.d/insidedcpulse.conf.ssl`, find the `location = /.well-known/mcp-registry-auth` block (just above the `# Public API` / catch-all `location /` block) and add this new block immediately after it, before `# Public API`:

```nginx
    # Human-readable landing page
    location = / {
        alias /usr/share/nginx/html/static/index.html;
    }
```

- [ ] **Step 2: Verify nginx config syntax**

Run: `docker run --rm -v "$(pwd)/docker/nginx:/etc/nginx/conf-check:ro" nginx:1.27-alpine nginx -t -c /etc/nginx/conf-check/conf.d/insidedcpulse.conf.ssl 2>&1 | tail -5`

(If this fails only because of unrelated paths like `ssl_certificate` not existing locally, that's expected/pre-existing — just confirm no NEW syntax error was introduced around the `location = /` block you added. If docker isn't available in this sandbox, skip this step — the block is a direct copy of the existing `location = /status` pattern, same syntax.)

- [ ] **Step 3: Commit**

```bash
git add docker/nginx/conf.d/insidedcpulse.conf.ssl
git commit -m "feat: serve static landing page at / via nginx"
```

---

### Task 3: Remove the JSON `/` route from the API

**Files:**
- Modify: `backend/app/main.py:67-76`

- [ ] **Step 1: Remove the `root()` route**

In `backend/app/main.py`, delete lines 67-76 (the `@app.get("/", tags=["meta"])` decorator, the `async def root():` function, and the blank lines separating it from the `metrics` route above and the `app.mount(...)` line below — leave exactly one blank line between `metrics` and the `app.mount` comment, matching the spacing pattern used elsewhere in the file).

Resulting tail of the file (from `metrics` onward) should read:

```python
@app.get("/metrics", tags=["meta"])
async def metrics():
    return metrics_response()


# Mounted last so it only matches paths not already handled by the routes above.
app.mount("/", MCPMethodGuardMiddleware(mcp.streamable_http_app()), name="mcp")
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/main.py
git commit -m "refactor: remove JSON / route, nginx now serves the landing page"
```

---

### Task 4: Update the test suite

**Files:**
- Modify: `backend/tests/test_mcp_mount.py:6-18`

- [ ] **Step 1: Rename the test and remove the `GET "/"` assertions**

In `backend/tests/test_mcp_mount.py`:
- Rename `def test_mcp_mounted_and_root_routes_not_shadowed():` (line 6) to `def test_mcp_mounted_and_handles_unknown_methods():`
- Delete lines 15-18:
  ```python
              r = client.get("/")
              assert r.status_code == 200
              assert r.json()["name"] == "InsideDCPulse"
              assert r.json()["status"] == "/status"

  ```
  (the blank line after them too, so the function body goes straight from `with TestClient(app) as client:` into the `r = client.post("/mcp", ...)` initialize call).

- [ ] **Step 2: Run the test suite**

Run: `cd backend && .venv/bin/pytest -q`
Expected: all tests pass (98 passed, same count as before — one test renamed/shrunk, none added/removed).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_mcp_mount.py
git commit -m "test: drop GET / JSON assertions, rename to reflect remaining coverage"
```

---

### Task 5: Fix the spec doc

**Files:**
- Modify: `docs/superpowers/specs/2026-06-11-landing-page-design.md`

The original spec doesn't mention what happens to `GET /` when the request hits the FastAPI app directly (bypassing nginx) — e.g. local dev/tests run against the backend without nginx in front. After Task 3, such a request falls through to the MCP mount (`app.mount("/", ...)`) instead of the old JSON. Document this as an intentional, out-of-scope-for-fixing edge case.

- [ ] **Step 1: Add a note to "Error Handling / Edge Cases"**

In `docs/superpowers/specs/2026-06-11-landing-page-design.md`, in the `## Error Handling / Edge Cases` section, add a new bullet after the existing `curl -H "Accept: application/json" ...` bullet:

```markdown
- Without nginx in front (e.g. hitting the FastAPI app directly in local
  dev/tests), `GET /` no longer returns the old JSON — it now falls through
  to the MCP mount (`app.mount("/", ...)` in `main.py`). This is intentional:
  in production nginx always intercepts `/` first, and no test or script
  hits the bare backend's `/` (confirmed in Task 4 of the implementation
  plan).
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-06-11-landing-page-design.md
git commit -m "docs: note GET / fallthrough behavior without nginx in spec"
```

---

## Self-Review Notes

- **Spec coverage**: Hero/links, Why, How it works, Get started, Get the code/connect, FAQ (all 8 Q&As), Footer → Task 1. nginx `location = /` → Task 2. `main.py` route removal → Task 3. `test_mcp_mount.py` update → Task 4. `sitemap.xml` → no change needed (spec confirms, already priority 1.0). Edge case clarification → Task 5.
- **Type/name consistency**: test renamed to `test_mcp_mounted_and_handles_unknown_methods` consistently between Task 4 step and spec's component 4 description.
- **No placeholders**: all file content is complete and final.
