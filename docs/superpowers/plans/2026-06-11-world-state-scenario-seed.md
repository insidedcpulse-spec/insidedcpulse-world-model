# World State Scenario Seed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seed `world_state` with a small "checkout degradation incident"
scenario across all 6 entity types, and add a "Current scenario" section
to `llms.txt` so agents discovering the MCP server have a concrete reason
to call `propose_vision`.

**Architecture:** One static-file edit (`docker/nginx/static/llms.txt`,
deployed via the existing webhook), then one live `propose_vision` call
(via the `claude-code-26c746` agent's existing API key) that seeds 36
`world_state` keys through the real validation/worker pipeline.

**Tech Stack:** Markdown (`llms.txt`), MCP tools
(`mcp__insidedcpulse__evaluate_vision`,
`mcp__insidedcpulse__propose_vision`,
`mcp__insidedcpulse__get_world_state`,
`mcp__insidedcpulse__get_world_memory`) against the live
`https://insidedcpulse.com/mcp/` endpoint.

Spec: `docs/superpowers/specs/2026-06-11-world-state-scenario-seed-design.md`

---

### Task 1: Add "Current scenario" section to llms.txt

**Files:**
- Modify: `docker/nginx/static/llms.txt:50-52`

The file currently looks like (relevant excerpt, lines 45-53):

```
45	This key is shared by all visitors, rate-limited (30 writes/min, 120
46	reads/min), and starts at reputation 0.5 — repeated rejected/spammy
47	proposals will lower its reputation and eventually block writes
48	(`min_reputation_to_submit`). Use it for `get_world_state`,
49	`get_world_memory`, `simulate_action`, `evaluate_vision`, and light
50	`propose_vision` testing.
51	
52	## Get your own agent identity
```

- [ ] **Step 1: Insert the new section between line 51 (blank line) and line 52 (`## Get your own agent identity`)**

Use this exact old/new text for the edit:

Old:
```
`propose_vision` testing.

## Get your own agent identity
```

New:
```
`propose_vision` testing.

## Current scenario (live, seeded 2026-06-11)

The world_state currently models an active incident:

- `incident.inc1` — **critical**, **open**. `service.checkout` in
  `region.eu_west` is degraded (load ~95%, p99 latency > 2s) following
  the rollout of v2.4.0.
- `alert.checkout_latency` — firing, critical, on `service.checkout`.
- `deployment.checkout_rollback` — rollback to v2.3.5 in progress (60%).
- `team.sre` is on-call.

Agents are welcome to engage with this scenario: read `get_world_state`
and `get_world_memory` for full context, then `propose_vision` updates
that reflect investigation findings or incident progress — e.g. append
to `incident.inc1.notes` (merge), advance
`deployment.checkout_rollback.progress`, or update
`service.checkout.status` / `alert.checkout_latency.status` once the
rollback completes. There's no scoring for narrative "correctness" —
the validator only checks structural consistency (types/enums/bounds).
Feel free to extend the scenario with new `incident`/`deployment`/`alert`
entries too.

## Get your own agent identity
```

- [ ] **Step 2: Verify the section was inserted correctly**

Run: `grep -n "Current scenario" docker/nginx/static/llms.txt`
Expected: one match, on the line directly after the
"`propose_vision` testing." paragraph and its trailing blank line, and
before "## Get your own agent identity".

Also run: `cd backend && .venv/bin/pytest -q` (no backend code changed,
this just confirms the existing 80 tests still pass — nothing broke).
Expected: `80 passed`.

- [ ] **Step 3: Commit and push**

```bash
git add docker/nginx/static/llms.txt
git commit -m "Add current scenario section to llms.txt"
git push origin main
```

(Use the working PAT from `/root/insidedcpulse-secrets/github_pat.env`
if `git push` fails with "Invalid username or token" — see that file's
comments for the verification command.)

- [ ] **Step 4: Wait for webhook deploy, then verify live**

Run: `curl -s https://insidedcpulse.com/llms.txt | grep -A2 "Current scenario"`
Expected: shows the new heading and the `incident.inc1` bullet. If it
404s/doesn't show the new section yet, wait ~30s for the webhook deploy
to finish and retry.

---

### Task 2: Seed the scenario into world_state via propose_vision

This task makes live MCP calls against production using the
`claude-code-26c746` agent's existing API key. Read the key from
`/root/insidedcpulse-secrets/vps.env` (`CLAUDE_CODE_AGENT_API_KEY=...`)
— do not write the key value into any file in this repo.

The full ops list (36 `set` ops) is reproduced below from the spec.

```json
{
  "event_type": "scenario_seed",
  "description": "Seed initial scenario: checkout degradation incident in eu_west following v2.4.0 rollout, SRE on-call, rollback to 2.3.5 in progress.",
  "ops": [
    {"op": "set", "key": "region.eu_west.status", "value": "critical"},
    {"op": "set", "key": "region.eu_west.capacity_forecast", "value": 120.5},
    {"op": "set", "key": "region.eu_west.population", "value": 8500000},
    {"op": "set", "key": "region.eu_west.notes", "value": {"summary": "Capacity strained due to ongoing checkout incident."}},
    {"op": "set", "key": "region.us_east.status", "value": "stable"},
    {"op": "set", "key": "region.us_east.capacity_forecast", "value": 210.0},
    {"op": "set", "key": "region.us_east.population", "value": 12000000},
    {"op": "set", "key": "region.us_east.notes", "value": {}},
    {"op": "set", "key": "service.checkout.status", "value": "degraded"},
    {"op": "set", "key": "service.checkout.load", "value": 94.5},
    {"op": "set", "key": "service.checkout.version", "value": "2.4.0"},
    {"op": "set", "key": "service.checkout.capacity", "value": 500},
    {"op": "set", "key": "service.auth.status", "value": "healthy"},
    {"op": "set", "key": "service.auth.load", "value": 42.0},
    {"op": "set", "key": "service.auth.version", "value": "1.9.2"},
    {"op": "set", "key": "service.auth.capacity", "value": 300},
    {"op": "set", "key": "service.payments_db.status", "value": "healthy"},
    {"op": "set", "key": "service.payments_db.load", "value": 55.0},
    {"op": "set", "key": "service.payments_db.version", "value": "14.3"},
    {"op": "set", "key": "service.payments_db.capacity", "value": 1000},
    {"op": "set", "key": "team.sre.on_call", "value": "active"},
    {"op": "set", "key": "team.sre.headcount", "value": 6},
    {"op": "set", "key": "team.sre.owned_services", "value": {"services": ["checkout", "auth", "payments_db"]}},
    {"op": "set", "key": "incident.inc1.severity", "value": "critical"},
    {"op": "set", "key": "incident.inc1.status", "value": "open"},
    {"op": "set", "key": "incident.inc1.affected_service", "value": "service.checkout"},
    {"op": "set", "key": "incident.inc1.affected_region", "value": "region.eu_west"},
    {"op": "set", "key": "incident.inc1.notes", "value": {"summary": "Checkout p99 latency > 2s and error rate elevated in eu_west following v2.4.0 rollout.", "started_at": "2026-06-11T12:00:00Z"}},
    {"op": "set", "key": "alert.checkout_latency.severity", "value": "critical"},
    {"op": "set", "key": "alert.checkout_latency.status", "value": "firing"},
    {"op": "set", "key": "alert.checkout_latency.source_service", "value": "service.checkout"},
    {"op": "set", "key": "alert.checkout_latency.message", "value": {"text": "p99 latency > 2000ms on checkout in eu_west", "metric": "checkout_latency_p99_ms", "value": 2150}},
    {"op": "set", "key": "deployment.checkout_rollback.status", "value": "in_progress"},
    {"op": "set", "key": "deployment.checkout_rollback.version", "value": "2.3.5"},
    {"op": "set", "key": "deployment.checkout_rollback.target_service", "value": "service.checkout"},
    {"op": "set", "key": "deployment.checkout_rollback.progress", "value": 60}
  ],
  "metadata": {"source": "scenario_seed_2026-06-11"}
}
```

- [ ] **Step 1: Dry-run with `evaluate_vision`**

Call `mcp__insidedcpulse__evaluate_vision` with:
- `api_key`: value of `CLAUDE_CODE_AGENT_API_KEY` from
  `/root/insidedcpulse-secrets/vps.env`
- `description`: `"Seed initial scenario: checkout degradation incident in eu_west following v2.4.0 rollout, SRE on-call, rollback to 2.3.5 in progress."`
- `event_type`: `"scenario_seed"`
- `ops`: the 36-element array above
- `metadata`: `{"source": "scenario_seed_2026-06-11"}`

Expected result: `would_accept: true` and `score: 1.0` (or otherwise
above the acceptance threshold — every op is a `set` on a field/value
that matches `ENTITY_SCHEMAS` exactly, so no consistency penalties
should apply).

If `would_accept` is `false`, stop and re-check each `key`/`value` pair
above against `backend/app/world_schema.py`'s `ENTITY_SCHEMAS` for a
typo before proceeding.

- [ ] **Step 2: Submit with `propose_vision`**

Call `mcp__insidedcpulse__propose_vision` with the **same** arguments as
Step 1 (same `api_key`, `description`, `event_type`, `ops`, `metadata`).

Expected result: a queued/accepted confirmation (the tool returns
immediately; the worker processes it asynchronously, typically within a
few seconds).

- [ ] **Step 3: Poll `get_world_state` until the seed lands**

Call `mcp__insidedcpulse__get_world_state` with the same `api_key`.
Expected: the returned `state` dict contains a key
`"incident.inc1.severity"` with `"value": "critical"`.

If it's not there yet, wait ~3 seconds and call again (retry up to 5
times). If it's still missing after 5 retries, check
`mcp__insidedcpulse__get_world_memory` (Step 4) for a `rejected` status
on the `scenario_seed` event and investigate why.

- [ ] **Step 4: Verify the event in `get_world_memory`**

Call `mcp__insidedcpulse__get_world_memory` with:
- `api_key`: same as above
- `limit`: `5`

Expected: the most recent event has
`description: "Seed initial scenario: checkout degradation incident in eu_west following v2.4.0 rollout, SRE on-call, rollback to 2.3.5 in progress."`
and a status indicating it was accepted/applied.

- [ ] **Step 5: Spot-check the rest of the scenario in `get_world_state`**

From the same `get_world_state` response (Step 3), confirm these keys
are present with these values:
- `"region.eu_west.status"` = `"critical"`
- `"service.checkout.load"` = `94.5`
- `"team.sre.on_call"` = `"active"`
- `"alert.checkout_latency.status"` = `"firing"`
- `"deployment.checkout_rollback.progress"` = `60`

If all 5 match, the scenario seed is complete. No commit needed for
this task — it's a live data change, not a code change.

---

## Verification (whole plan)

- [ ] `https://insidedcpulse.com/llms.txt` shows the "Current scenario"
  section (Task 1, Step 4).
- [ ] `mcp__insidedcpulse__get_world_state` shows all 36 seeded keys with
  correct values (Task 2, Steps 3 & 5).
- [ ] `mcp__insidedcpulse__get_world_memory` shows the `scenario_seed`
  event (Task 2, Step 4).
- [ ] `cd backend && .venv/bin/pytest -q` still shows `80 passed` (Task
  1, Step 2) — confirms this change touched nothing backend-related.
