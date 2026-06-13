# Graph Memory Projection Layer — Design

## Why

InsideDCPulse is event-sourced: agents propose visions, a deterministic
worker validates them, accepted events are appended to the `events` table
(single source of truth), and `world_state` is a materialized projection
rebuilt by replaying accepted events.

Inspired by MAGMA (multi-graph agentic memory architectures), we want a
**second projection** — a graph of nodes/edges capturing temporal, causal,
and relational structure across agents, events, and domain entities
(region/service/incident/deployment/team/alert/research/finding). This lets
agents and operators answer questions like "what led to this incident?",
"what has deploy-agent proposed recently?", "what's the causal chain behind
this alert?" — without scanning the entire event log.

## Critical invariants (non-negotiable)

1. The `events` table (append-only) remains the **single source of truth**.
2. `world_state` (existing projection), the validation pipeline, the
   reputation system, and the MCP server are **unchanged** in behavior.
3. The Graph Memory is **derived only from accepted events**, in the same
   transaction as `world_state`'s `commit_ops` — both projections come from
   the same event, atomically.
4. No agent and no API ever writes directly to `graph_nodes`/`graph_edges`.
   The only writer is `graph_projection.project_event()`, called from the
   worker (live) and from `rebuild_graph_projection.py` (replay).
5. Everything stays fully replayable: `TRUNCATE` + replay all accepted
   events in `id` order must reproduce an identical graph (determinism,
   auditability).

## Architecture

```
Agent -> Vision -> deterministic validation -> accepted event
                                                     |
                                                     v
                                          events (append-only, source of truth)
                                                     |
                              +----------------------+----------------------+
                              v                                              v
                    World State Projection                    Graph Memory Projection
                    (commit_ops, existing)                     (project_event, new)
                              |                                              |
                              +-------------------+--------------------------+
                                                   v
                                        single DB transaction
                                     (worker.process_event, accepted branch)
```

Rejected events touch neither projection (matches existing `world_state`
semantics — see "Non-goals").

## Data model

### Node types

`graph_nodes.type` is derived dynamically, not a hardcoded enum:

- `agent` — id = `agent_id` (e.g. `sre-agent-212dbc`)
- `event` — id = `event.<events.id>` (e.g. `event.139`)
- one type per key in `world_schema.ENTITY_SCHEMAS` — id = `<entity>.<entity_id>`
  (e.g. `incident.inc3`, `service.checkout`, `team.sre`)

No separate generic "Entity" type is needed: `world_schema.parse_key()`
already filters which `world_state` keys belong to a known domain entity.
Ops on non-domain keys (e.g. legacy `demo.counter`) are skipped by the graph
projection, same as they're skipped by domain validation.

### Entity reference values

`world_schema.parse_key()` parses 3-segment `world_state` *keys*
(`<entity>.<entity_id>.<field>`). Reference *values* (e.g.
`incident.inc3.affected_service = "service.checkout"`) are 2-segment
`<entity>.<entity_id>`. The projection adds a small sibling helper,
`parse_entity_ref(value) -> (entity, entity_id) | None`, in
`graph_projection.py`: matches `^([a-z][a-z0-9_]*)\.([a-z0-9_]{1,32})$` with
`entity in ENTITY_SCHEMAS`. Used by R1 and the generic `REFERENCES` fields
below.

### Edge types — active in MVP

| edge_type    | source -> target            | created when |
|--------------|------------------------------|--------------|
| `PROPOSED`   | Agent -> Event                | always, for every accepted event |
| `AFFECTED`   | Event -> Entity               | once per distinct entity touched by the event's ops (deduped) |
| `PRECEDES`   | Event_n -> Event_{n+1}        | global chain over `events.id` for accepted events, one direction only (`FOLLOWS` = reverse traversal of the same edge, not stored separately) |
| `REFERENCES` | Entity -> Entity              | generic cross-entity fields (`affected_service`, `affected_region`, `source_service`, `target_service`) whose value parses via `parse_entity_ref` |
| `OWNED_BY`   | Service -> Team               | from `team.<X>.owned_services` (see below) |
| `CAUSED`     | Entity -> Entity               | deterministic rules R1-R3, see below |

### Edge types — reserved, not created in MVP

`VALIDATED`, `REJECTED`, `RELATED_TO`, `MENTIONS` — documented for future
phases (e.g. `REJECTED` would require also projecting rejected events,
`MENTIONS` would require free-text entity extraction from `research.*`/
`finding.*`). Listing them here so the edge_type column isn't constrained by
an enum and future work doesn't need a migration to add them.

## Causal rules (CAUSED edges)

All rules are pure functions of (a) the current accepted event's ops/diff
and (b) the graph state built by strictly earlier events (lower `events.id`)
— so replay in `id` order is deterministic and reproducible.

### R1 — Explicit reference (confidence 1.0)

A field whose name ends in `_deployment_id`, `_incident_id`, or `_alert_id`
and whose value parses as a valid `<entity>.<entity_id>` ref (via
`parse_entity_ref`, defined above) creates:

```
<entity owning the field> -CAUSED-> <referenced entity>
```

confidence = `1.0`, `metadata.rule_id = "explicit_ref"`.

Example: `incident.inc3.notes.scaling_deployment_id = "deployment.checkout_scaling"`
-> `Incident:inc3 -CAUSED-> Deployment:checkout_scaling`.

### OWNED_BY field shape

`team.<T>.owned_services` is a fixed-shape object: `{"services": [<bare
service id>, ...]}` (live example: `team.sre.owned_services =
{"services": ["checkout", "auth", "payments_db"]}` — items are bare ids,
*not* `<entity>.<id>` refs, since the entity type is fixed to `service` by
the field's definition). For each item `S`: upsert `service.S` node (if not
already present from other events) and edge `service.S -OWNED_BY->
team.T`.

### R2 — Alert firing precedes Incident open (heuristic)

Trigger: an op sets `incident.<I>.status = "open"` where the **before**
value (from `commit_ops`'s `applied` diff) was `None` (first time this
incident is opened).

Search: `alert.<A>` nodes with a prior accepted event (`event_id` <
current) that set `alert.<A>.status = "firing"`, where `alert.A` has a
`REFERENCES` edge to the same `service`/`region` as `incident.I`'s
`affected_service`/`affected_region`, within a lookback window `W = 50`
accepted events.

On match: `Alert:A -CAUSED-> Incident:I`, `metadata.rule_id =
"alert_precedes_incident"`.

```
confidence = base * recency
base       = 0.7 if same affected_service, else 0.5 if same affected_region
recency    = max(0.3, 1 - dist / W)
dist       = event_id(incident_open_event) - event_id(alert_firing_event)
```

### R3 — Deployment precedes Service degradation (heuristic)

Trigger: an op sets `service.<S>.status = "degraded"` where **before** !=
`"degraded"`.

Search: `deployment.<D>` with a `REFERENCES` edge `target_service ==
service.S`, and a prior accepted event (`event_id` < current) that set
`deployment.D.status` to `"in_progress"` or `"done"`, within lookback window
`W = 50`.

On match: `Deployment:D -CAUSED-> Service:S`, `metadata.rule_id =
"deployment_precedes_degradation"`.

```
confidence = 0.7 * recency   (same recency formula as R2, W = 50)
```

### Rule table extensibility

R1-R3 live in a small ordered list (`CAUSAL_RULES`) in
`graph_projection.py`, each entry a function `(conn, db_id, payload, applied)
-> list[CausalEdge]`. Adding R4+ later is additive — no schema change
needed (`edge_type='CAUSED'`, `metadata.rule_id` discriminates).

## Storage schema

New tables in `docker/postgres/init.sql` (applied to fresh deploys) +
manual `ALTER`/`CREATE TABLE IF NOT EXISTS` against the live DB (same
pattern as the `agents.created_via` column addition):

```sql
CREATE TABLE IF NOT EXISTS graph_nodes (
    id          TEXT PRIMARY KEY,        -- "agent.sre-agent-212dbc" | "event.139" | "incident.inc3"
    type        TEXT NOT NULL,           -- "agent" | "event" | "<entity>"
    label       TEXT NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON graph_nodes(type);

CREATE TABLE IF NOT EXISTS graph_edges (
    id              BIGSERIAL PRIMARY KEY,
    source_node     TEXT NOT NULL REFERENCES graph_nodes(id),
    target_node     TEXT NOT NULL REFERENCES graph_nodes(id),
    edge_type       TEXT NOT NULL,
    weight          NUMERIC(5,4) NOT NULL DEFAULT 1.0,  -- confidence for CAUSED, count for AFFECTED
    metadata        JSONB NOT NULL DEFAULT '{}',        -- rule_id, evidence event ids, etc.
    source_event_id BIGINT REFERENCES events(id),       -- which event (last) created/refreshed this edge
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_node, target_node, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_node, edge_type);
CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_node, edge_type);
```

`UNIQUE(source_node, target_node, edge_type)` + `ON CONFLICT DO UPDATE`
(refresh `weight`/`metadata`/`source_event_id`/`created_at`) makes the
projection idempotent — required for replay correctness (no duplicate
edges) and for live updates that touch an existing edge (e.g. `AFFECTED`
weight incrementing, `CAUSED` confidence recomputation as more evidence
accrues).

## Worker integration

`backend/app/worker.py`, accepted branch, same transaction as `commit_ops`:

```python
async with pool.acquire() as conn:
    async with conn.transaction():
        db_id = await mark_processed(conn, event_id, "accepted", score, reason_text)
        applied = await commit_ops(conn, payload.ops, db_id)
        await graph_projection.project_event(conn, db_id, agent_id, payload, applied)
        new_reputation = await apply_outcome(conn, agent_id, True)
```

`project_event` signature:

```python
async def project_event(
    conn: asyncpg.Connection,
    event_db_id: int,
    agent_id: str,
    payload: VisionRequest,
    applied: dict[str, dict],  # {key: {before, after}} from commit_ops
) -> None:
```

Internally: upsert `agent` node, upsert `event` node (label =
`payload.description`, metadata = `{score, event_type}`), `PROPOSED` edge,
`PRECEDES` edge from the previous accepted event (if any), then for each
key in `applied` whose `parse_key()` succeeds: upsert entity node, `AFFECTED`
edge, `REFERENCES`/`OWNED_BY` edges from the new value's reference fields,
then run `CAUSAL_RULES`.

## Replay / rebuild

`scripts/rebuild_graph_projection.py`:

```python
TRUNCATE graph_nodes, graph_edges RESTART IDENTITY;

for event in SELECT * FROM events WHERE status = 'accepted' ORDER BY id:
    applied = <recompute before/after by replaying world_state up to this event>
    await project_event(conn, event.id, event.agent_id, event.payload, applied)
```

Reuses the exact same `project_event` used live — single source of
projection logic, guaranteeing the rebuilt graph matches the live one
byte-for-byte (modulo `created_at`/`updated_at` timestamps).

## Sub-project A scope (this implementation plan)

- Migration: `graph_nodes`/`graph_edges` (init.sql + live `ALTER`/`CREATE
  TABLE IF NOT EXISTS` via the same manual-apply pattern as prior schema
  changes)
- `backend/app/projections/__init__.py`, `backend/app/projections/graph_projection.py`
  — `project_event()`, `CAUSAL_RULES` (R1-R3), node/edge upsert helpers
- `worker.py` integration (same transaction)
- `scripts/rebuild_graph_projection.py`
- Tests (`backend/tests/test_graph_projection.py`):
  - node/edge creation per op type (PROPOSED, AFFECTED, PRECEDES, REFERENCES, OWNED_BY)
  - R1 explicit-reference CAUSED edge
  - R2 alert->incident heuristic (positive + no-match cases)
  - R3 deployment->service-degradation heuristic
  - idempotency (`ON CONFLICT DO UPDATE`, re-running `project_event` doesn't duplicate)
  - replay determinism: `rebuild_graph_projection.py` on a fixture event log
    reproduces the same nodes/edges as incremental live projection

## Sub-project B — Graph Query API + MCP tools (future plan, documented here)

### REST endpoints (`backend/app/routers/graph.py`, prefix `/api/v1/graph`, `rate_limited(READ)`)

- `GET /node/{id}` — node + its direct edges (in/out, grouped by type)
- `GET /neighbors/{id}?edge_type=&direction=out|in|both&limit=`
- `GET /path?from=&to=&max_depth=` — bounded BFS shortest path (default
  `max_depth=6`, hard cap 10)
- `GET /timeline?entity=&limit=` — ordered `Event -AFFECTED-> entity` history
  (or global event chain via `PRECEDES` if `entity` omitted)
- `GET /causal-chain?node=&direction=upstream|downstream&max_depth=` —
  traverse `CAUSED` edges

### MCP tools (`mcp_server.py`, read-only, same `_authenticate`/rate-limit pattern)

- `get_graph_node`
- `get_graph_neighbors`
- `get_event_timeline`
- `get_causal_chain`
- `find_related_entities`

All five are thin wrappers around the REST handlers' query functions. None
can write.

## Sub-project C — Observability + docs (future plan, documented here)

### Grafana dashboards (new, alongside existing 5)

- Graph Growth (node/edge count over time)
- Node/Edge counts by type
- Most Connected Entities (degree ranking)
- Agent Interaction Map (Agent -PROPOSED-> Event -AFFECTED-> Entity)
- Temporal Traversal Metrics (PRECEDES chain depth/query latency)
- Causal Chain Metrics (CAUSED edge count by `rule_id`, avg confidence)

### Docs / rollout

- README: new "Graph Memory Projection" section (schema tables, edge types,
  causal rules, API/MCP surface)
- `llms.txt`: mention `get_graph_*`/`get_causal_chain`/`find_related_entities`
  tools once Sub-project B ships
- Rollout plan: Sub-project A ships first (silent — new tables, no API
  surface yet, zero risk to existing behavior); B and C ship once A is
  live-verified via `rebuild_graph_projection.py` against real production
  events.

## Non-goals (explicitly deferred)

- Projecting rejected events (`REJECTED`/full agent-attempt history) —
  would need a second worker hook outside `commit_ops`'s transaction scope.
- `RELATED_TO`/`MENTIONS` edges (free-text entity extraction).
- Per-entity `PRECEDES` chains (entity history is answered via `AFFECTED`
  edges ordered by event id — no separate chain needed).
- Neo4j or any graph database — PostgreSQL only, per requirements.
- Additional `CAUSED` rules beyond R1-R3 (rule table is additive; more
  rules are a follow-up once R1-R3 are live-verified).

## Success criteria

- ✔ Event Store remains sole source of truth, unchanged
- ✔ World State Projection unchanged
- ✔ Graph Memory Projection derived atomically from the same accepted
  events, in the same transaction
- ✔ `rebuild_graph_projection.py` reproduces the live graph from scratch
  (determinism/auditability)
- ✔ Temporal navigation (`PRECEDES`/`AFFECTED` timelines)
- ✔ Causal navigation (`CAUSED` chains with confidence + `rule_id`)
- ✔ Agent/entity relationship navigation (`PROPOSED`, `REFERENCES`, `OWNED_BY`)
- ✔ No agent or component writes to the graph directly
- ✔ Sub-project A ships with zero behavior change to existing
  endpoints/MCP tools/validation/reputation
