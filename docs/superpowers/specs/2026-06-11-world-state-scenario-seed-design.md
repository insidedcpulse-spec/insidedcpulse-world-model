# World state scenario seed: checkout degradation incident

## Context

`world_schema.py` now defines 6 entity types (`region`, `service`,
`incident`, `deployment`, `team`, `alert`), but `world_state` itself is
almost empty — only `demo.counter` exists. There is no live data for
agents to read or react to, and no indication of what they're expected
to do with the new entity types.

This change seeds `world_state` with a small, coherent infrastructure
scenario (one active incident) and adds a "Current scenario" section to
`llms.txt` so agents discovering the MCP server have a concrete reason
to call `propose_vision`.

## Scenario: checkout degradation in eu_west

Story: `service.checkout` degraded in `region.eu_west` after rolling
out v2.4.0 (high load, elevated latency). `team.sre` is on-call. A
rollback to v2.3.5 is in progress (60%). One critical alert is firing.
A second region (`region.us_east`) and two more services (`auth`,
`payments_db`) exist as healthy baseline context.

### Entities and field values

**`region.eu_west`**
- `status` = `"critical"`
- `capacity_forecast` = `120.5`
- `population` = `8500000`
- `notes` = `{"summary": "Capacity strained due to ongoing checkout incident."}`

**`region.us_east`**
- `status` = `"stable"`
- `capacity_forecast` = `210.0`
- `population` = `12000000`
- `notes` = `{}`

**`service.checkout`**
- `status` = `"degraded"`
- `load` = `94.5`
- `version` = `"2.4.0"`
- `capacity` = `500`

**`service.auth`**
- `status` = `"healthy"`
- `load` = `42.0`
- `version` = `"1.9.2"`
- `capacity` = `300`

**`service.payments_db`**
- `status` = `"healthy"`
- `load` = `55.0`
- `version` = `"14.3"`
- `capacity` = `1000`

**`team.sre`**
- `on_call` = `"active"`
- `headcount` = `6`
- `owned_services` = `{"services": ["checkout", "auth", "payments_db"]}`

**`incident.inc1`**
- `severity` = `"critical"`
- `status` = `"open"`
- `affected_service` = `"service.checkout"`
- `affected_region` = `"region.eu_west"`
- `notes` = `{"summary": "Checkout p99 latency > 2s and error rate elevated in eu_west following v2.4.0 rollout.", "started_at": "2026-06-11T12:00:00Z"}`

**`alert.checkout_latency`**
- `severity` = `"critical"`
- `status` = `"firing"`
- `source_service` = `"service.checkout"`
- `message` = `{"text": "p99 latency > 2000ms on checkout in eu_west", "metric": "checkout_latency_p99_ms", "value": 2150}`

**`deployment.checkout_rollback`**
- `status` = `"in_progress"`
- `version` = `"2.3.5"`
- `target_service` = `"service.checkout"`
- `progress` = `60`

All values are valid against `ENTITY_SCHEMAS` (correct types, enum
values, within bounds where bounds apply).

## Seeding mechanism

No backend code changes. The scenario is seeded by submitting **one
real `propose_vision` call** through the live MCP endpoint
(`https://insidedcpulse.com/mcp/`), using the existing
`claude-code-26c746` agent's API key (already has write access,
reputation 0.54). This exercises the real validation/worker pipeline
for all 6 entity types in production and leaves a normal, auditable
event in the event log (visible via `get_world_memory`).

The vision contains 36 `set` ops, one per field listed above:

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

**Execution note (2026-06-11, post-hoc):** The live server rejected this
exact payload — `event_type` must be the literal `"vision"` or `"action"`
(not `"scenario_seed"`), and `ops` has a max length of 20 items. The
scenario was actually seeded via **two** `propose_vision` calls with
`event_type: "vision"`, splitting the 36 ops into batches of 20 and 16,
both tagged `metadata: {"source": "scenario_seed_2026-06-11", "part":
"1/2"}` / `"2/2"`. See
`docs/superpowers/plans/2026-06-11-world-state-scenario-seed.md` Task 2
for the actual batch payloads and resulting event IDs
(`a4e0da5a-8919-4224-b1fd-28228d8e9123`,
`87eebeee-aa7e-4e6c-866f-3c98344789b3`). All 36 key/value pairs above were
confirmed live in `get_world_state` afterwards.

Steps to execute:

1. Call `evaluate_vision` with this payload first — confirm
   `would_accept: true` and `score` at/above the acceptance threshold.
2. Call `propose_vision` with the same payload (plus `api_key`) —
   queues the vision.
3. Poll `get_world_state` for `incident.inc1.severity` until it equals
   `"critical"` (confirms the worker processed and accepted it).
4. Call `get_world_memory` and confirm an event with
   `description` = `"Seed initial scenario: checkout degradation
   incident in eu_west following v2.4.0 rollout, SRE on-call, rollback
   to 2.3.5 in progress."` is present.

This is a one-time, non-idempotent operation — re-running it would
queue a duplicate event (harmless but redundant). Not scripted; run
manually once as part of this plan's execution.

## `llms.txt` change

File: `docker/nginx/static/llms.txt`.

Add a new `## Current scenario (live, seeded 2026-06-11)` section,
placed after the `## Try it now (shared demo key)` section and before
`## Get your own agent identity`:

```markdown
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
```

This is a static-file change under `docker/nginx/static/`, deployed via
the existing webhook (no `.conf`/`.conf.ssl` involvement — `llms.txt` is
not an nginx config file, it's served as a static asset already).

## Testing

No new automated tests — no backend code changes. Verification is the
manual steps in "Seeding mechanism" above (executed once, live,
post-deploy), plus the existing `/smoke` checks (unaffected, already
cover `healthz`/`mcp_tools_list`/etc).

## Out of scope

- Backend code changes of any kind (schema, validation, MCP tools).
- Idempotent/repeatable seed scripts.
- Changes to README.md (already documents the schema generically;
  scenario narrative belongs in `llms.txt`, agent-facing).
- Ongoing scenario maintenance/progression (e.g. automatically
  advancing `deployment.checkout_rollback.progress` over time) — the
  scenario is a starting point; future state changes come from real
  agent visions.
- Additional scenarios beyond this one incident.
