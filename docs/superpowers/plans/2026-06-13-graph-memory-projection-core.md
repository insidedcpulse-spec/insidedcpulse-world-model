# Graph Memory Projection — Sub-project A (Core Projection) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second, derived "Graph Memory" projection (`graph_nodes`/`graph_edges` in Postgres) that is populated atomically from the same accepted events that feed `world_state`, with deterministic causal-inference rules (R1-R3) and a full replay/rebuild script — with zero behavior change to the existing event store, world state, validation, reputation, or MCP server.

**Architecture:** New module `backend/app/projections/graph_projection.py` exposes `project_event(conn, event_db_id, agent_id, payload, applied)`, called once from `worker.py`'s existing accepted-branch transaction (right after `commit_ops`). It upserts `agent`/`event`/`<entity>` nodes and `PROPOSED`/`AFFECTED`/`PRECEDES`/`REFERENCES`/`OWNED_BY`/`CAUSED` edges, all via `INSERT ... ON CONFLICT DO UPDATE` for idempotency. `scripts/rebuild_graph_projection.py` replays all accepted events through the exact same `project_event()` after `TRUNCATE`, guaranteeing determinism.

**Tech Stack:** Python 3.12, asyncpg, FastAPI/pydantic (existing `VisionRequest`/`WorldOp` schemas), pytest + pytest-asyncio with `unittest.mock.AsyncMock` (existing convention — "no real Postgres needed").

**Spec:** `docs/superpowers/specs/2026-06-13-graph-memory-projection-design.md` (read this for the *why* behind each rule — this plan implements its "Sub-project A scope" section only).

---

## File Structure

- **Modify:** `docker/postgres/init.sql` — append `graph_nodes`/`graph_edges` table definitions + indexes (Task 1)
- **Create:** `backend/app/projections/__init__.py` — empty package marker (Task 2)
- **Create:** `backend/app/projections/graph_projection.py` — `parse_entity_ref`, node/edge upsert helpers, `project_event()`, `CAUSAL_RULES` (R1-R3) (Tasks 2-7)
- **Create:** `backend/tests/test_graph_projection.py` — all new tests (Tasks 2-7, 9)
- **Modify:** `backend/app/worker.py` — call `graph_projection.project_event(...)` in the accepted-branch transaction (Task 8)
- **Create:** `backend/tests/test_worker.py` — worker integration test for the new call (Task 8)
- **Create:** `scripts/rebuild_graph_projection.py` — TRUNCATE + replay (Task 9)

---

## Task 1: Migration — `graph_nodes` / `graph_edges` tables

**Files:**
- Modify: `docker/postgres/init.sql`

- [ ] **Step 1: Append the new table definitions to `init.sql`**

Add this block at the end of `docker/postgres/init.sql` (after the `drift_samples` index, before the genesis-agent `INSERT`):

```sql
-- Graph Memory Projection: derived only from accepted events (see
-- backend/app/projections/graph_projection.py). Never written directly.
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
    metadata        JSONB NOT NULL DEFAULT '{}',        -- rule_id, evidence event ids, fields touched, etc.
    source_event_id BIGINT REFERENCES events(id),       -- which event (last) created/refreshed this edge
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_node, target_node, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_node, edge_type);
CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_node, edge_type);
```

- [ ] **Step 2: Apply the migration to the live database**

```bash
cd /root/insidedcpulse-world-model/docker
docker compose exec -T postgres psql -U insidedcpulse -d insidedcpulse <<'SQL'
CREATE TABLE IF NOT EXISTS graph_nodes (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
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
    weight          NUMERIC(5,4) NOT NULL DEFAULT 1.0,
    metadata        JSONB NOT NULL DEFAULT '{}',
    source_event_id BIGINT REFERENCES events(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_node, target_node, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_node, edge_type);
CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_node, edge_type);
SQL
```

Expected: `CREATE TABLE` / `CREATE INDEX` (or silent no-op if already applied) for each statement, no errors.

- [ ] **Step 3: Verify the tables exist**

```bash
docker compose exec -T postgres psql -U insidedcpulse -d insidedcpulse -c "\d graph_nodes" -c "\d graph_edges"
```

Expected: both `\d` outputs show the columns listed above, including the `UNIQUE` constraint on `graph_edges(source_node, target_node, edge_type)`.

- [ ] **Step 4: Commit**

```bash
git add docker/postgres/init.sql
git commit -m "feat: add graph_nodes/graph_edges schema for Graph Memory Projection"
```

---

## Task 2: Project skeleton — `parse_entity_ref` + node/edge upsert helpers

**Files:**
- Create: `backend/app/projections/__init__.py`
- Create: `backend/app/projections/graph_projection.py`
- Test: `backend/tests/test_graph_projection.py`

- [ ] **Step 1: Create the empty package marker**

`backend/app/projections/__init__.py`:

```python
```

(empty file)

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_graph_projection.py`:

```python
import json
from unittest.mock import AsyncMock, call

import pytest

from app.projections.graph_projection import (
    UPSERT_NODE_SQL,
    UPSERT_EDGE_SQL,
    ENSURE_NODE_SQL,
    EntityRef,
    parse_entity_ref,
    _ensure_node,
    _upsert_edge,
    _upsert_node,
)


def test_parse_entity_ref_valid():
    assert parse_entity_ref("service.checkout") == EntityRef("service", "checkout")


def test_parse_entity_ref_unknown_entity():
    assert parse_entity_ref("widget.foo") is None


def test_parse_entity_ref_three_segments_is_not_a_ref():
    assert parse_entity_ref("incident.inc3.status") is None


def test_parse_entity_ref_non_string():
    assert parse_entity_ref(42) is None


@pytest.mark.asyncio
async def test_upsert_node_derives_type_and_label_from_id():
    conn = AsyncMock()

    await _upsert_node(conn, "incident.inc3")

    conn.execute.assert_awaited_once_with(
        UPSERT_NODE_SQL, "incident.inc3", "incident", "incident.inc3", "{}"
    )


@pytest.mark.asyncio
async def test_upsert_node_agent_label_strips_prefix():
    conn = AsyncMock()

    await _upsert_node(conn, "agent.sre-agent-212dbc")

    conn.execute.assert_awaited_once_with(
        UPSERT_NODE_SQL, "agent.sre-agent-212dbc", "agent", "sre-agent-212dbc", "{}"
    )


@pytest.mark.asyncio
async def test_upsert_node_explicit_type_label_metadata():
    conn = AsyncMock()

    await _upsert_node(conn, "event.139", "event", "Deploy checkout v2", {"event_type": "vision"})

    conn.execute.assert_awaited_once_with(
        UPSERT_NODE_SQL, "event.139", "event", "Deploy checkout v2",
        json.dumps({"event_type": "vision"}),
    )


@pytest.mark.asyncio
async def test_ensure_node_uses_do_nothing_sql():
    conn = AsyncMock()

    await _ensure_node(conn, "deployment.checkout_scaling")

    conn.execute.assert_awaited_once_with(
        ENSURE_NODE_SQL, "deployment.checkout_scaling", "deployment", "deployment.checkout_scaling"
    )


@pytest.mark.asyncio
async def test_upsert_edge_defaults():
    conn = AsyncMock()

    await _upsert_edge(conn, "agent.sre-agent-212dbc", "event.139", "PROPOSED", source_event_id=139)

    conn.execute.assert_awaited_once_with(
        UPSERT_EDGE_SQL, "agent.sre-agent-212dbc", "event.139", "PROPOSED", 1.0, "{}", 139
    )


@pytest.mark.asyncio
async def test_upsert_edge_with_weight_and_metadata():
    conn = AsyncMock()

    await _upsert_edge(
        conn, "alert.a1", "incident.inc3", "CAUSED",
        weight=0.56, metadata={"rule_id": "alert_precedes_incident"}, source_event_id=200,
    )

    conn.execute.assert_awaited_once_with(
        UPSERT_EDGE_SQL, "alert.a1", "incident.inc3", "CAUSED", 0.56,
        json.dumps({"rule_id": "alert_precedes_incident"}), 200,
    )
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd backend && .venv/bin/pytest tests/test_graph_projection.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.projections.graph_projection'` (or import error) for all tests.

- [ ] **Step 4: Implement `graph_projection.py` skeleton**

`backend/app/projections/graph_projection.py`:

```python
"""Graph Memory Projection: a second, derived projection over accepted events.

Populated only by project_event(), called from worker.py (live) and
scripts/rebuild_graph_projection.py (replay). Never written by agents or
API handlers directly. See
docs/superpowers/specs/2026-06-13-graph-memory-projection-design.md.
"""

import json
import re
from typing import NamedTuple

from app.world_schema import ENTITY_SCHEMAS, parse_key

ENTITY_REF_PATTERN = re.compile(r"^([a-z][a-z0-9_]*)\.([a-z0-9_]{1,32})$")

REFERENCE_FIELDS = {"affected_service", "affected_region", "source_service", "target_service"}
R1_SUFFIXES = ("_deployment_id", "_incident_id", "_alert_id")
CAUSAL_WINDOW = 50


class EntityRef(NamedTuple):
    entity: str
    entity_id: str


class CausalEdge(NamedTuple):
    source: str
    target: str
    confidence: float
    metadata: dict


def parse_entity_ref(value) -> EntityRef | None:
    """Parse a 2-segment '<entity>.<entity_id>' reference value (not a world_state key)."""
    if not isinstance(value, str):
        return None
    match = ENTITY_REF_PATTERN.match(value)
    if not match:
        return None
    entity, entity_id = match.groups()
    if entity not in ENTITY_SCHEMAS:
        return None
    return EntityRef(entity, entity_id)


def _node_type_and_label(node_id: str) -> tuple[str, str]:
    prefix, _, rest = node_id.partition(".")
    if prefix == "agent":
        return "agent", rest
    return prefix, node_id


UPSERT_NODE_SQL = """
    INSERT INTO graph_nodes (id, type, label, metadata, updated_at)
    VALUES ($1, $2, $3, $4::jsonb, now())
    ON CONFLICT (id) DO UPDATE
    SET label = $3, metadata = $4::jsonb, updated_at = now()
"""

ENSURE_NODE_SQL = """
    INSERT INTO graph_nodes (id, type, label)
    VALUES ($1, $2, $3)
    ON CONFLICT (id) DO NOTHING
"""

UPSERT_EDGE_SQL = """
    INSERT INTO graph_edges (source_node, target_node, edge_type, weight, metadata, source_event_id)
    VALUES ($1, $2, $3, $4, $5::jsonb, $6)
    ON CONFLICT (source_node, target_node, edge_type) DO UPDATE
    SET weight = $4, metadata = $5::jsonb, source_event_id = $6
"""


async def _upsert_node(conn, node_id, node_type=None, label=None, metadata=None):
    if node_type is None or label is None:
        derived_type, derived_label = _node_type_and_label(node_id)
        node_type = node_type or derived_type
        label = label or derived_label
    await conn.execute(UPSERT_NODE_SQL, node_id, node_type, label, json.dumps(metadata or {}))


async def _ensure_node(conn, node_id):
    node_type, label = _node_type_and_label(node_id)
    await conn.execute(ENSURE_NODE_SQL, node_id, node_type, label)


async def _upsert_edge(conn, source, target, edge_type, weight=1.0, metadata=None, source_event_id=None):
    await conn.execute(
        UPSERT_EDGE_SQL, source, target, edge_type, weight, json.dumps(metadata or {}), source_event_id
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && .venv/bin/pytest tests/test_graph_projection.py -v
```

Expected: all tests in `test_graph_projection.py` PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/projections/__init__.py backend/app/projections/graph_projection.py backend/tests/test_graph_projection.py
git commit -m "feat: add graph_projection skeleton (parse_entity_ref, node/edge upsert helpers)"
```

---

## Task 3: `project_event` core — agent/event nodes, PROPOSED, PRECEDES

**Files:**
- Modify: `backend/app/projections/graph_projection.py`
- Test: `backend/tests/test_graph_projection.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_graph_projection.py`:

```python
from app.projections.graph_projection import PREV_EVENT_SQL, project_event
from app.schemas import VisionRequest, WorldOp


def _vision(description="Do a thing", ops=None, event_type="vision"):
    return VisionRequest(
        event_type=event_type,
        description=description,
        ops=ops or [WorldOp(op="set", key="demo.counter", value=1)],
    )


@pytest.mark.asyncio
async def test_project_event_upserts_agent_event_and_proposed_edge():
    conn = AsyncMock()
    conn.fetchrow.return_value = None  # no previous accepted event
    conn.fetch.return_value = []

    payload = _vision(description="Bump the demo counter")
    await project_event(conn, 42, "sre-agent-212dbc", payload, applied={})

    conn.execute.assert_any_await(
        UPSERT_NODE_SQL, "agent.sre-agent-212dbc", "agent", "sre-agent-212dbc", "{}"
    )
    conn.execute.assert_any_await(
        UPSERT_NODE_SQL, "event.42", "event", "Bump the demo counter",
        json.dumps({"event_type": "vision"}),
    )
    conn.execute.assert_any_await(
        UPSERT_EDGE_SQL, "agent.sre-agent-212dbc", "event.42", "PROPOSED", 1.0, "{}", 42
    )
    conn.fetchrow.assert_awaited_once_with(PREV_EVENT_SQL, 42)


@pytest.mark.asyncio
async def test_project_event_no_precedes_edge_when_no_prior_event():
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    conn.fetch.return_value = []

    await project_event(conn, 1, "sre-agent-212dbc", _vision(), applied={})

    precedes_calls = [
        c for c in conn.execute.await_args_list if c.args[0] == UPSERT_EDGE_SQL and c.args[3] == "PRECEDES"
    ]
    assert precedes_calls == []


@pytest.mark.asyncio
async def test_project_event_precedes_edge_from_prior_accepted_event():
    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": 41}
    conn.fetch.return_value = []

    await project_event(conn, 42, "sre-agent-212dbc", _vision(), applied={})

    conn.execute.assert_any_await(
        UPSERT_EDGE_SQL, "event.41", "event.42", "PRECEDES", 1.0, "{}", 42
    )
```

> Note: `AsyncMock.assert_any_await` checks any call in `await_args_list` matches — available on `unittest.mock` since Python 3.8.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && .venv/bin/pytest tests/test_graph_projection.py -v -k project_event
```

Expected: `ImportError: cannot import name 'project_event'` (and `PREV_EVENT_SQL`).

- [ ] **Step 3: Implement `project_event` core**

Append to `backend/app/projections/graph_projection.py` (after the upsert helpers, before any rule functions — rules are added in later tasks):

```python
PREV_EVENT_SQL = """
    SELECT id FROM events WHERE status = 'accepted' AND id < $1 ORDER BY id DESC LIMIT 1
"""


async def project_event(conn, event_db_id, agent_id, payload, applied: dict[str, dict]) -> None:
    agent_node_id = f"agent.{agent_id}"
    await _upsert_node(conn, agent_node_id)

    event_node_id = f"event.{event_db_id}"
    await _upsert_node(
        conn, event_node_id, "event", payload.description, {"event_type": payload.event_type}
    )
    await _upsert_edge(conn, agent_node_id, event_node_id, "PROPOSED", source_event_id=event_db_id)

    prev = await conn.fetchrow(PREV_EVENT_SQL, event_db_id)
    if prev is not None:
        await _upsert_edge(
            conn, f"event.{prev['id']}", event_node_id, "PRECEDES", source_event_id=event_db_id
        )

    for edge in await _causal_edges(conn, event_db_id, payload, applied):
        await _ensure_node(conn, edge.source)
        await _ensure_node(conn, edge.target)
        await _upsert_edge(
            conn, edge.source, edge.target, "CAUSED",
            weight=edge.confidence, metadata=edge.metadata, source_event_id=event_db_id,
        )


async def _causal_edges(conn, event_db_id, payload, applied) -> list:
    edges: list = []
    for rule in CAUSAL_RULES:
        edges.extend(await rule(conn, event_db_id, payload, applied))
    return edges


CAUSAL_RULES: list = []
```

`CAUSAL_RULES` starts empty here; Tasks 5-7 each append one rule function to it. This keeps `project_event` stable while rules are added incrementally — the `conn.fetch.return_value = []` in this task's tests is never actually consulted yet (no rules to call `conn.fetch`), but is included for forward-compatibility with later tasks' tests that reuse `_vision()`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && .venv/bin/pytest tests/test_graph_projection.py -v
```

Expected: all tests PASS (including Task 2's).

- [ ] **Step 5: Commit**

```bash
git add backend/app/projections/graph_projection.py backend/tests/test_graph_projection.py
git commit -m "feat: project_event core (agent/event nodes, PROPOSED, PRECEDES)"
```

---

## Task 4: AFFECTED, REFERENCES, OWNED_BY edges from `applied`

**Files:**
- Modify: `backend/app/projections/graph_projection.py`
- Test: `backend/tests/test_graph_projection.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_graph_projection.py`:

```python
@pytest.mark.asyncio
async def test_project_event_affected_edge_with_field_weight_and_metadata():
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    conn.fetch.return_value = []

    applied = {
        "incident.inc3.status": {"before": "open", "after": "mitigated"},
        "incident.inc3.severity": {"before": "high", "after": "medium"},
    }
    await project_event(conn, 42, "sre-agent-212dbc", _vision(), applied)

    conn.execute.assert_any_await(
        UPSERT_NODE_SQL, "incident.inc3", "incident", "incident.inc3", "{}"
    )
    conn.execute.assert_any_await(
        UPSERT_EDGE_SQL, "event.42", "incident.inc3", "AFFECTED", 2,
        json.dumps({"fields": {"status": "mitigated", "severity": "medium"}}), 42,
    )


@pytest.mark.asyncio
async def test_project_event_references_edge_for_affected_service():
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    conn.fetch.return_value = []

    applied = {
        "incident.inc3.affected_service": {"before": None, "after": "service.checkout"},
    }
    await project_event(conn, 50, "sre-agent-212dbc", _vision(), applied)

    conn.execute.assert_any_await(
        UPSERT_NODE_SQL, "service.checkout", "service", "service.checkout", "{}"
    )
    conn.execute.assert_any_await(
        UPSERT_EDGE_SQL, "incident.inc3", "service.checkout", "REFERENCES", 1.0, "{}", 50
    )


@pytest.mark.asyncio
async def test_project_event_skips_non_domain_keys():
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    conn.fetch.return_value = []

    applied = {"demo.counter": {"before": 0, "after": 1}}
    await project_event(conn, 7, "sre-agent-212dbc", _vision(), applied)

    affected_calls = [
        c for c in conn.execute.await_args_list if c.args[0] == UPSERT_EDGE_SQL and c.args[3] == "AFFECTED"
    ]
    assert affected_calls == []


@pytest.mark.asyncio
async def test_project_event_owned_by_edges_from_team_owned_services():
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    conn.fetch.return_value = []

    applied = {
        "team.sre.owned_services": {
            "before": None,
            "after": {"services": ["checkout", "auth", "payments_db"]},
        },
    }
    await project_event(conn, 60, "sre-agent-212dbc", _vision(), applied)

    for svc in ("checkout", "auth", "payments_db"):
        conn.execute.assert_any_await(
            UPSERT_NODE_SQL, f"service.{svc}", "service", f"service.{svc}", "{}"
        )
        conn.execute.assert_any_await(
            UPSERT_EDGE_SQL, f"service.{svc}", "team.sre", "OWNED_BY", 1.0, "{}", 60
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && .venv/bin/pytest tests/test_graph_projection.py -v -k "affected or references or owned_by or non_domain"
```

Expected: FAIL — `assert_any_await` finds no matching call (AFFECTED/REFERENCES/OWNED_BY edges not yet produced).

- [ ] **Step 3: Implement the `applied`-keys loop**

In `backend/app/projections/graph_projection.py`, insert this block in `project_event`, **after** the PRECEDES block and **before** the `_causal_edges` call:

```python
    affected_counts: dict[str, int] = {}
    affected_fields: dict[str, dict] = {}
    for key, change in applied.items():
        parts = parse_key(key)
        if parts is None:
            continue
        entity_node_id = f"{parts.entity}.{parts.entity_id}"
        affected_counts[entity_node_id] = affected_counts.get(entity_node_id, 0) + 1
        affected_fields.setdefault(entity_node_id, {})[parts.field] = change["after"]
        await _upsert_node(conn, entity_node_id)

    for entity_node_id, count in affected_counts.items():
        await _upsert_edge(
            conn, event_node_id, entity_node_id, "AFFECTED",
            weight=count, metadata={"fields": affected_fields[entity_node_id]},
            source_event_id=event_db_id,
        )

    for key, change in applied.items():
        parts = parse_key(key)
        if parts is None:
            continue
        entity_node_id = f"{parts.entity}.{parts.entity_id}"
        after = change["after"]

        if parts.field in REFERENCE_FIELDS:
            ref = parse_entity_ref(after)
            if ref is not None:
                target_id = f"{ref.entity}.{ref.entity_id}"
                await _upsert_node(conn, target_id)
                await _upsert_edge(conn, entity_node_id, target_id, "REFERENCES", source_event_id=event_db_id)

        if parts.entity == "team" and parts.field == "owned_services" and isinstance(after, dict):
            for svc in after.get("services", []):
                svc_node_id = f"service.{svc}"
                await _upsert_node(conn, svc_node_id)
                await _upsert_edge(conn, svc_node_id, entity_node_id, "OWNED_BY", source_event_id=event_db_id)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && .venv/bin/pytest tests/test_graph_projection.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/projections/graph_projection.py backend/tests/test_graph_projection.py
git commit -m "feat: project AFFECTED/REFERENCES/OWNED_BY edges from applied ops"
```

---

## Task 5: CAUSAL_RULES R1 — explicit reference (confidence 1.0)

**Files:**
- Modify: `backend/app/projections/graph_projection.py`
- Test: `backend/tests/test_graph_projection.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_graph_projection.py`:

```python
from app.projections.graph_projection import _rule_r1_explicit_ref


@pytest.mark.asyncio
async def test_r1_explicit_ref_in_notes_field():
    conn = AsyncMock()
    applied = {
        "incident.inc3.notes": {
            "before": {},
            "after": {"scaling_deployment_id": "deployment.checkout_scaling"},
        },
    }

    edges = await _rule_r1_explicit_ref(conn, 100, _vision(), applied)

    assert edges == [
        CausalEdge("incident.inc3", "deployment.checkout_scaling", 1.0, {"rule_id": "explicit_ref"})
    ]


@pytest.mark.asyncio
async def test_r1_no_match_when_notes_has_no_ref_suffix_field():
    conn = AsyncMock()
    applied = {
        "incident.inc3.notes": {"before": {}, "after": {"summary": "investigating"}},
    }

    edges = await _rule_r1_explicit_ref(conn, 100, _vision(), applied)

    assert edges == []


@pytest.mark.asyncio
async def test_r1_no_match_when_ref_value_does_not_parse():
    conn = AsyncMock()
    applied = {
        "incident.inc3.notes": {
            "before": {},
            "after": {"scaling_deployment_id": "not-a-ref"},
        },
    }

    edges = await _rule_r1_explicit_ref(conn, 100, _vision(), applied)

    assert edges == []


@pytest.mark.asyncio
async def test_project_event_creates_caused_edge_for_r1():
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    conn.fetch.return_value = []

    applied = {
        "incident.inc3.notes": {
            "before": {},
            "after": {"scaling_deployment_id": "deployment.checkout_scaling"},
        },
    }
    await project_event(conn, 100, "sre-agent-212dbc", _vision(), applied)

    conn.execute.assert_any_await(
        ENSURE_NODE_SQL, "incident.inc3", "incident", "incident.inc3"
    )
    conn.execute.assert_any_await(
        ENSURE_NODE_SQL, "deployment.checkout_scaling", "deployment", "deployment.checkout_scaling"
    )
    conn.execute.assert_any_await(
        UPSERT_EDGE_SQL, "incident.inc3", "deployment.checkout_scaling", "CAUSED", 1.0,
        json.dumps({"rule_id": "explicit_ref"}), 100,
    )
```

Add `CausalEdge` and `ENSURE_NODE_SQL` to the import line at the top of the test file (extend the existing `from app.projections.graph_projection import (...)` block):

```python
from app.projections.graph_projection import (
    CausalEdge,
    ENSURE_NODE_SQL,
    EntityRef,
    PREV_EVENT_SQL,
    UPSERT_EDGE_SQL,
    UPSERT_NODE_SQL,
    _ensure_node,
    _rule_r1_explicit_ref,
    _upsert_edge,
    _upsert_node,
    parse_entity_ref,
    project_event,
)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && .venv/bin/pytest tests/test_graph_projection.py -v -k r1
```

Expected: `ImportError: cannot import name '_rule_r1_explicit_ref'`.

- [ ] **Step 3: Implement R1 and register it in `CAUSAL_RULES`**

Append to `backend/app/projections/graph_projection.py`:

```python
async def _rule_r1_explicit_ref(conn, event_db_id, payload, applied) -> list[CausalEdge]:
    edges: list[CausalEdge] = []
    for key, change in applied.items():
        parts = parse_key(key)
        if parts is None:
            continue
        after = change["after"]
        if parts.field == "notes" and isinstance(after, dict):
            candidates = after.items()
        else:
            candidates = [(parts.field, after)]

        for field_name, value in candidates:
            if not field_name.endswith(R1_SUFFIXES):
                continue
            ref = parse_entity_ref(value)
            if ref is None:
                continue
            source_id = f"{parts.entity}.{parts.entity_id}"
            target_id = f"{ref.entity}.{ref.entity_id}"
            edges.append(CausalEdge(source_id, target_id, 1.0, {"rule_id": "explicit_ref"}))
    return edges
```

Then update the `CAUSAL_RULES` list (defined at the end of Task 3's edit) from:

```python
CAUSAL_RULES: list = []
```

to:

```python
CAUSAL_RULES: list = [_rule_r1_explicit_ref]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && .venv/bin/pytest tests/test_graph_projection.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/projections/graph_projection.py backend/tests/test_graph_projection.py
git commit -m "feat: CAUSAL_RULES R1 - explicit *_deployment_id/_incident_id/_alert_id references"
```

---

## Task 6: CAUSAL_RULES R2 — Alert firing precedes Incident open

**Files:**
- Modify: `backend/app/projections/graph_projection.py`
- Test: `backend/tests/test_graph_projection.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_graph_projection.py`:

```python
from app.projections.graph_projection import RECENT_AFFECTED_SQL, REFERENCES_FROM_SQL, _rule_r2_alert_precedes_incident


@pytest.mark.asyncio
async def test_r2_matches_alert_firing_for_same_service():
    conn = AsyncMock()
    conn.fetch.side_effect = [
        [{"target_node": "service.checkout"}],  # incident.inc3's REFERENCES targets
        [{"target_node": "alert.a1", "source_event_id": 190, "metadata": {"fields": {"status": "firing"}}}],
        [{"target_node": "service.checkout"}],  # alert.a1's REFERENCES targets
    ]
    applied = {"incident.inc3.status": {"before": None, "after": "open"}}

    edges = await _rule_r2_alert_precedes_incident(conn, 200, _vision(), applied)

    assert edges == [
        CausalEdge("alert.a1", "incident.inc3", 0.7 * 0.8, {
            "rule_id": "alert_precedes_incident", "evidence_event_id": 190,
        })
    ]
    conn.fetch.assert_any_await(REFERENCES_FROM_SQL, "incident.inc3")
    conn.fetch.assert_any_await(RECENT_AFFECTED_SQL, "alert.%", 200, CAUSAL_WINDOW)
    conn.fetch.assert_any_await(REFERENCES_FROM_SQL, "alert.a1")


@pytest.mark.asyncio
async def test_r2_no_match_when_incident_has_no_references():
    conn = AsyncMock()
    conn.fetch.side_effect = [[]]  # incident.inc3 has no REFERENCES edges
    applied = {"incident.inc3.status": {"before": None, "after": "open"}}

    edges = await _rule_r2_alert_precedes_incident(conn, 200, _vision(), applied)

    assert edges == []


@pytest.mark.asyncio
async def test_r2_skips_when_status_was_already_set():
    conn = AsyncMock()
    applied = {"incident.inc3.status": {"before": "open", "after": "mitigated"}}

    edges = await _rule_r2_alert_precedes_incident(conn, 200, _vision(), applied)

    assert edges == []
    conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_r2_no_match_when_no_common_reference_target():
    conn = AsyncMock()
    conn.fetch.side_effect = [
        [{"target_node": "service.checkout"}],
        [{"target_node": "alert.a1", "source_event_id": 190, "metadata": {"fields": {"status": "firing"}}}],
        [{"target_node": "service.payments"}],  # alert.a1 references a different service
    ]
    applied = {"incident.inc3.status": {"before": None, "after": "open"}}

    edges = await _rule_r2_alert_precedes_incident(conn, 200, _vision(), applied)

    assert edges == []
```

Add `CAUSAL_WINDOW` to the imports from `app.projections.graph_projection`.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && .venv/bin/pytest tests/test_graph_projection.py -v -k r2
```

Expected: `ImportError: cannot import name 'RECENT_AFFECTED_SQL'` (and `_rule_r2_alert_precedes_incident`).

- [ ] **Step 3: Implement R2 and its supporting SQL constants**

Append to `backend/app/projections/graph_projection.py` (SQL constants near the top alongside `PREV_EVENT_SQL`, the rest near R1):

```python
REFERENCES_FROM_SQL = """
    SELECT target_node FROM graph_edges WHERE source_node = $1 AND edge_type = 'REFERENCES'
"""

REFERENCES_TO_SQL = """
    SELECT source_node FROM graph_edges
    WHERE target_node = $1 AND edge_type = 'REFERENCES' AND source_node LIKE $2
"""

RECENT_AFFECTED_SQL = """
    SELECT target_node, source_event_id, metadata FROM graph_edges
    WHERE edge_type = 'AFFECTED' AND target_node LIKE $1
      AND source_event_id < $2 AND source_event_id >= $2 - $3
"""


def _recency(dist: int, window: int) -> float:
    return max(0.3, 1 - dist / window)


async def _rule_r2_alert_precedes_incident(conn, event_db_id, payload, applied) -> list[CausalEdge]:
    edges: list[CausalEdge] = []
    for key, change in applied.items():
        parts = parse_key(key)
        if parts is None or parts.entity != "incident" or parts.field != "status":
            continue
        if change["after"] != "open" or change["before"] is not None:
            continue
        incident_id = f"incident.{parts.entity_id}"

        ref_rows = await conn.fetch(REFERENCES_FROM_SQL, incident_id)
        ref_targets = {row["target_node"] for row in ref_rows}
        if not ref_targets:
            continue

        affected_rows = await conn.fetch(RECENT_AFFECTED_SQL, "alert.%", event_db_id, CAUSAL_WINDOW)
        for row in affected_rows:
            fields = (row["metadata"] or {}).get("fields", {})
            if fields.get("status") != "firing":
                continue
            alert_id = row["target_node"]

            alert_ref_rows = await conn.fetch(REFERENCES_FROM_SQL, alert_id)
            alert_targets = {r["target_node"] for r in alert_ref_rows}
            common = ref_targets & alert_targets
            if not common:
                continue

            base = 0.7 if any(t.startswith("service.") for t in common) else 0.5
            dist = event_db_id - row["source_event_id"]
            confidence = base * _recency(dist, CAUSAL_WINDOW)
            edges.append(CausalEdge(alert_id, incident_id, confidence, {
                "rule_id": "alert_precedes_incident",
                "evidence_event_id": row["source_event_id"],
            }))
    return edges
```

Update `CAUSAL_RULES` to:

```python
CAUSAL_RULES: list = [_rule_r1_explicit_ref, _rule_r2_alert_precedes_incident]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && .venv/bin/pytest tests/test_graph_projection.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/projections/graph_projection.py backend/tests/test_graph_projection.py
git commit -m "feat: CAUSAL_RULES R2 - alert-firing precedes incident-open heuristic"
```

---

## Task 7: CAUSAL_RULES R3 — Deployment precedes Service degradation

**Files:**
- Modify: `backend/app/projections/graph_projection.py`
- Test: `backend/tests/test_graph_projection.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_graph_projection.py`:

```python
from app.projections.graph_projection import REFERENCES_TO_SQL, _rule_r3_deployment_precedes_degradation


@pytest.mark.asyncio
async def test_r3_matches_deployment_in_progress_before_degradation():
    conn = AsyncMock()
    conn.fetch.side_effect = [
        [{"source_node": "deployment.checkout_v2"}],  # deployments referencing service.checkout
        [{"target_node": "deployment.checkout_v2", "source_event_id": 295, "metadata": {"fields": {"status": "in_progress"}}}],
    ]
    applied = {"service.checkout.status": {"before": "healthy", "after": "degraded"}}

    edges = await _rule_r3_deployment_precedes_degradation(conn, 300, _vision(), applied)

    assert edges == [
        CausalEdge("deployment.checkout_v2", "service.checkout", 0.7 * 0.9, {
            "rule_id": "deployment_precedes_degradation", "evidence_event_id": 295,
        })
    ]
    conn.fetch.assert_any_await(REFERENCES_TO_SQL, "service.checkout", "deployment.%")
    conn.fetch.assert_any_await(RECENT_AFFECTED_SQL, "deployment.%", 300, CAUSAL_WINDOW)


@pytest.mark.asyncio
async def test_r3_skips_when_already_degraded():
    conn = AsyncMock()
    applied = {"service.checkout.status": {"before": "degraded", "after": "degraded"}}

    edges = await _rule_r3_deployment_precedes_degradation(conn, 300, _vision(), applied)

    assert edges == []
    conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_r3_no_match_when_no_referencing_deployment():
    conn = AsyncMock()
    conn.fetch.side_effect = [[]]
    applied = {"service.checkout.status": {"before": "healthy", "after": "degraded"}}

    edges = await _rule_r3_deployment_precedes_degradation(conn, 300, _vision(), applied)

    assert edges == []


@pytest.mark.asyncio
async def test_r3_no_match_when_deployment_status_not_in_progress_or_done():
    conn = AsyncMock()
    conn.fetch.side_effect = [
        [{"source_node": "deployment.checkout_v2"}],
        [{"target_node": "deployment.checkout_v2", "source_event_id": 295, "metadata": {"fields": {"status": "pending"}}}],
    ]
    applied = {"service.checkout.status": {"before": "healthy", "after": "degraded"}}

    edges = await _rule_r3_deployment_precedes_degradation(conn, 300, _vision(), applied)

    assert edges == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && .venv/bin/pytest tests/test_graph_projection.py -v -k r3
```

Expected: `ImportError: cannot import name 'REFERENCES_TO_SQL'` (and `_rule_r3_deployment_precedes_degradation`).

- [ ] **Step 3: Implement R3**

Append to `backend/app/projections/graph_projection.py`:

```python
async def _rule_r3_deployment_precedes_degradation(conn, event_db_id, payload, applied) -> list[CausalEdge]:
    edges: list[CausalEdge] = []
    for key, change in applied.items():
        parts = parse_key(key)
        if parts is None or parts.entity != "service" or parts.field != "status":
            continue
        if change["after"] != "degraded" or change["before"] == "degraded":
            continue
        service_id = f"service.{parts.entity_id}"

        dep_rows = await conn.fetch(REFERENCES_TO_SQL, service_id, "deployment.%")
        deployment_ids = {row["source_node"] for row in dep_rows}
        if not deployment_ids:
            continue

        affected_rows = await conn.fetch(RECENT_AFFECTED_SQL, "deployment.%", event_db_id, CAUSAL_WINDOW)
        for row in affected_rows:
            deployment_id = row["target_node"]
            if deployment_id not in deployment_ids:
                continue
            fields = (row["metadata"] or {}).get("fields", {})
            if fields.get("status") not in ("in_progress", "done"):
                continue
            dist = event_db_id - row["source_event_id"]
            confidence = 0.7 * _recency(dist, CAUSAL_WINDOW)
            edges.append(CausalEdge(deployment_id, service_id, confidence, {
                "rule_id": "deployment_precedes_degradation",
                "evidence_event_id": row["source_event_id"],
            }))
    return edges
```

Update `CAUSAL_RULES` to:

```python
CAUSAL_RULES: list = [
    _rule_r1_explicit_ref,
    _rule_r2_alert_precedes_incident,
    _rule_r3_deployment_precedes_degradation,
]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && .venv/bin/pytest tests/test_graph_projection.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/projections/graph_projection.py backend/tests/test_graph_projection.py
git commit -m "feat: CAUSAL_RULES R3 - deployment precedes service degradation heuristic"
```

---

## Task 8: Worker integration — same transaction as `commit_ops`

**Files:**
- Modify: `backend/app/worker.py`
- Test: `backend/tests/test_worker.py` (new)

- [ ] **Step 1: Write the failing test**

`backend/tests/test_worker.py`:

```python
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas import VisionRequest, WorldOp
from app.worker import process_event


@pytest.mark.asyncio
async def test_process_event_accepted_calls_project_event_in_same_transaction():
    event_id = uuid.uuid4()
    agent_id = "sre-agent-212dbc"
    payload = VisionRequest(
        event_type="vision",
        description="Mark incident mitigated",
        ops=[WorldOp(op="set", key="incident.inc3.status", value="mitigated")],
    )
    data = {"event_id": str(event_id), "agent_id": agent_id, "payload": payload.model_dump()}

    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "id": agent_id, "reputation": 0.6, "total_submitted": 10, "total_accepted": 8, "total_rejected": 2,
    }

    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    r = AsyncMock()
    r.get.return_value = None

    applied = {"incident.inc3.status": {"before": "open", "after": "mitigated"}}

    with (
        patch("app.worker.evaluate", new=AsyncMock(return_value=(0.9, True, ["looks good"]))),
        patch("app.worker.mark_processed", new=AsyncMock(return_value=99)),
        patch("app.worker.commit_ops", new=AsyncMock(return_value=applied)),
        patch("app.worker.apply_outcome", new=AsyncMock(return_value=0.62)),
        patch("app.worker.graph_projection.project_event", new=AsyncMock()) as project_event_mock,
    ):
        await process_event(pool, r, data)

    project_event_mock.assert_awaited_once_with(conn, 99, agent_id, payload, applied)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && .venv/bin/pytest tests/test_worker.py -v
```

Expected: `AttributeError: <module 'app.worker'> does not have the attribute 'graph_projection'`.

- [ ] **Step 3: Wire `graph_projection.project_event` into the accepted branch**

In `backend/app/worker.py`, add the import (alongside the other `app.*` imports near the top):

```python
from app.projections import graph_projection
```

Then in `process_event`'s accepted branch (currently):

```python
    if accept:
        async with pool.acquire() as conn:
            async with conn.transaction():
                db_id = await mark_processed(conn, event_id, "accepted", score, reason_text)
                applied = await commit_ops(conn, payload.ops, db_id)
                new_reputation = await apply_outcome(conn, agent_id, True)
```

change to:

```python
    if accept:
        async with pool.acquire() as conn:
            async with conn.transaction():
                db_id = await mark_processed(conn, event_id, "accepted", score, reason_text)
                applied = await commit_ops(conn, payload.ops, db_id)
                await graph_projection.project_event(conn, db_id, agent_id, payload, applied)
                new_reputation = await apply_outcome(conn, agent_id, True)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && .venv/bin/pytest tests/test_worker.py tests/test_graph_projection.py -v
```

Expected: all PASS.

- [ ] **Step 5: Run the full backend test suite to confirm zero regressions**

```bash
cd backend && .venv/bin/pytest tests/ -v
```

Expected: all tests PASS (existing suite unaffected — `graph_projection.project_event` is additive).

- [ ] **Step 6: Commit**

```bash
git add backend/app/worker.py backend/tests/test_worker.py
git commit -m "feat: call graph_projection.project_event from worker's accepted-event transaction"
```

---

## Task 9: `scripts/rebuild_graph_projection.py` + replay determinism test

**Files:**
- Create: `scripts/rebuild_graph_projection.py`
- Test: `backend/tests/test_graph_projection.py`

This task adds a small in-memory fake connection (`FakeConn`) used **only** by the replay-determinism test, since `project_event` issues several distinct SQL shapes (node/edge upserts, PRECEDES lookup, R2/R3 graph queries) that need to behave consistently across two independent runs (live-incremental vs. rebuild-replay) to prove determinism. All other tests in this file continue to use plain `AsyncMock` per the project's existing convention.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_graph_projection.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from app.world_state import apply_op_to_value  # noqa: E402


class FakeConn:
    """Minimal in-memory stand-in for asyncpg.Connection, covering exactly the
    SQL shapes graph_projection.py issues. Used only for the replay-determinism
    test below."""

    def __init__(self, events):
        self.events = events  # [{"id": int, "status": "accepted"}]
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str, str], dict] = {}

    @staticmethod
    def _norm(sql):
        return " ".join(sql.split())

    async def execute(self, sql, *args):
        s = self._norm(sql)
        if s.startswith("TRUNCATE"):
            self.nodes.clear()
            self.edges.clear()
        elif "INSERT INTO graph_nodes (id, type, label, metadata" in s:
            node_id, node_type, label, metadata_json = args
            self.nodes[node_id] = {"type": node_type, "label": label, "metadata": json.loads(metadata_json)}
        elif "INSERT INTO graph_nodes (id, type, label)" in s:
            node_id, node_type, label = args
            self.nodes.setdefault(node_id, {"type": node_type, "label": label, "metadata": {}})
        elif "INSERT INTO graph_edges" in s:
            source, target, edge_type, weight, metadata_json, source_event_id = args
            self.edges[(source, target, edge_type)] = {
                "weight": weight, "metadata": json.loads(metadata_json), "source_event_id": source_event_id,
            }
        else:
            raise AssertionError(f"unexpected SQL in FakeConn.execute: {s!r}")

    async def fetchrow(self, sql, *args):
        s = self._norm(sql)
        if "FROM events" in s:
            (before_id,) = args
            candidates = [e for e in self.events if e["status"] == "accepted" and e["id"] < before_id]
            if not candidates:
                return None
            return {"id": max(e["id"] for e in candidates)}
        raise AssertionError(f"unexpected SQL in FakeConn.fetchrow: {s!r}")

    async def fetch(self, sql, *args):
        s = self._norm(sql)
        if "SELECT target_node FROM graph_edges WHERE source_node" in s:
            (source,) = args
            return [
                {"target_node": t} for (src, t, et) in self.edges if src == source and et == "REFERENCES"
            ]
        if "SELECT source_node FROM graph_edges WHERE target_node" in s:
            target, prefix = args
            pfx = prefix.rstrip("%")
            return [
                {"source_node": src} for (src, tgt, et) in self.edges
                if tgt == target and et == "REFERENCES" and src.startswith(pfx)
            ]
        if "WHERE edge_type = 'AFFECTED'" in s:
            prefix, before_id, window = args
            pfx = prefix.rstrip("%")
            out = []
            for (src, tgt, et), data in self.edges.items():
                if et == "AFFECTED" and tgt.startswith(pfx):
                    sev = data["source_event_id"]
                    if sev < before_id and sev >= before_id - window:
                        out.append({"target_node": tgt, "source_event_id": sev, "metadata": data["metadata"]})
            return out
        raise AssertionError(f"unexpected SQL in FakeConn.fetch: {s!r}")


def _fixture_events():
    return [
        (1, "deploy-agent-aaaaaa", VisionRequest(
            event_type="vision",
            description="Start checkout v2 deployment",
            ops=[
                WorldOp(op="set", key="deployment.checkout_v2.status", value="in_progress"),
                WorldOp(op="set", key="deployment.checkout_v2.target_service", value="service.checkout"),
            ],
        )),
        (2, "alert-agent-bbbbbb", VisionRequest(
            event_type="vision",
            description="Checkout latency alert firing",
            ops=[
                WorldOp(op="set", key="alert.a1.status", value="firing"),
                WorldOp(op="set", key="alert.a1.source_service", value="service.checkout"),
            ],
        )),
        (3, "sre-agent-cccccc", VisionRequest(
            event_type="vision",
            description="Open incident for checkout",
            ops=[
                WorldOp(op="set", key="incident.inc3.status", value="open"),
                WorldOp(op="set", key="incident.inc3.affected_service", value="service.checkout"),
            ],
        )),
    ]


@pytest.mark.asyncio
async def test_rebuild_reproduces_live_projection():
    from rebuild_graph_projection import rebuild_from_events

    fixture = _fixture_events()
    events_meta = [{"id": event_id, "status": "accepted"} for event_id, _, _ in fixture]

    live = FakeConn(events_meta)
    world_state: dict[str, object] = {}
    for event_id, agent_id, payload in fixture:
        applied = {}
        for op in payload.ops:
            before = world_state.get(op.key)
            after = None if op.op == "delete" else apply_op_to_value(before, op)
            applied[op.key] = {"before": before, "after": after}
            if op.op == "delete":
                world_state.pop(op.key, None)
            else:
                world_state[op.key] = after
        await project_event(live, event_id, agent_id, payload, applied)

    rebuilt = FakeConn(events_meta)
    await rebuild_from_events(rebuilt, [
        {"id": event_id, "agent_id": agent_id, "payload": payload} for event_id, agent_id, payload in fixture
    ])

    assert rebuilt.nodes == live.nodes
    assert rebuilt.edges == live.edges
    # Sanity: R2 should have fired (alert.a1 -CAUSED-> incident.inc3)
    assert ("alert.a1", "incident.inc3", "CAUSED") in live.edges
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && .venv/bin/pytest tests/test_graph_projection.py -v -k rebuild
```

Expected: `ModuleNotFoundError: No module named 'rebuild_graph_projection'`.

- [ ] **Step 3: Implement `scripts/rebuild_graph_projection.py`**

```python
#!/usr/bin/env python3
"""Rebuild the Graph Memory Projection from scratch by replaying accepted events.

TRUNCATEs graph_nodes/graph_edges and re-derives them by calling the exact
same project_event() used live by backend/app/worker.py — guaranteeing the
rebuilt graph matches the live one (modulo created_at/updated_at timestamps).

Run inside the api container (needs the `app` package + DB access):

    docker compose exec api python scripts/rebuild_graph_projection.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")  # backend/ root inside the api container

import asyncpg  # noqa: E402

from app.config import settings  # noqa: E402
from app.projections.graph_projection import project_event  # noqa: E402
from app.schemas import VisionRequest, WorldOp  # noqa: E402
from app.world_state import apply_op_to_value  # noqa: E402

TRUNCATE_SQL = "TRUNCATE graph_nodes, graph_edges RESTART IDENTITY"

SELECT_ACCEPTED_SQL = """
    SELECT id, agent_id, payload FROM events WHERE status = 'accepted' ORDER BY id
"""


async def rebuild_from_events(conn, events: list[dict]) -> None:
    """events: [{"id": int, "agent_id": str, "payload": VisionRequest}, ...] in id order."""
    world_state: dict[str, object] = {}

    for event in events:
        payload = event["payload"]
        applied: dict[str, dict] = {}
        for op in payload.ops:
            before = world_state.get(op.key)
            if op.op == "delete":
                after = None
                world_state.pop(op.key, None)
            else:
                after = apply_op_to_value(before, op)
                world_state[op.key] = after
            applied[op.key] = {"before": before, "after": after}

        await project_event(conn, event["id"], event["agent_id"], payload, applied)


async def main() -> None:
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=1)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(TRUNCATE_SQL)
                rows = await conn.fetch(SELECT_ACCEPTED_SQL)
                events = []
                for row in rows:
                    payload_raw = row["payload"]
                    payload_dict = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                    events.append({
                        "id": row["id"],
                        "agent_id": row["agent_id"],
                        "payload": VisionRequest(**payload_dict),
                    })
                await rebuild_from_events(conn, events)
        print(f"Rebuilt graph projection from {len(events)} accepted events.")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
```

> Note: `WorldOp` is imported but unused directly in this script — it's part of `VisionRequest`'s `ops` field type and may be needed if you extend this script later. If your linter flags unused imports, remove the `WorldOp` import; it is not required by the code above.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && .venv/bin/pytest tests/test_graph_projection.py -v
```

Expected: all tests PASS, including `test_rebuild_reproduces_live_projection`.

- [ ] **Step 5: Run the full backend suite one more time**

```bash
cd backend && .venv/bin/pytest tests/ -v
```

Expected: all PASS.

- [ ] **Step 6: Make the script executable and commit**

```bash
chmod +x scripts/rebuild_graph_projection.py
git add scripts/rebuild_graph_projection.py backend/tests/test_graph_projection.py
git commit -m "feat: add rebuild_graph_projection.py + replay determinism test"
```

---

## Task 10: Apply rebuild against the live database (verification)

**Files:** none (operational verification only)

- [ ] **Step 1: Run the rebuild script against the live DB**

```bash
cd /root/insidedcpulse-world-model/docker
docker compose exec -T api python scripts/rebuild_graph_projection.py
```

Expected: `Rebuilt graph projection from <N> accepted events.` with no errors. (If `scripts/` is not mounted into the `api` container, first add a bind mount for the repo's `scripts/` directory to `/app/scripts:ro` under the `api` service in `docker/docker-compose.yml`, then `docker compose up -d api`.)

- [ ] **Step 2: Spot-check the populated graph**

```bash
docker compose exec -T postgres psql -U insidedcpulse -d insidedcpulse -c \
  "SELECT type, count(*) FROM graph_nodes GROUP BY type ORDER BY type;"
docker compose exec -T postgres psql -U insidedcpulse -d insidedcpulse -c \
  "SELECT edge_type, count(*) FROM graph_edges GROUP BY edge_type ORDER BY edge_type;"
docker compose exec -T postgres psql -U insidedcpulse -d insidedcpulse -c \
  "SELECT source_node, target_node, weight, metadata->>'rule_id' AS rule_id FROM graph_edges WHERE edge_type = 'CAUSED' ORDER BY weight DESC LIMIT 10;"
```

Expected: non-zero node/edge counts across `agent`, `event`, and domain entity types (`incident`, `service`, `deployment`, `alert`, `region`, `team`, `research`, `finding` as applicable to live data); `CAUSED` edges (if any) show plausible `rule_id`/`weight` values.

- [ ] **Step 3: Re-run the script and confirm idempotency**

```bash
docker compose exec -T api python scripts/rebuild_graph_projection.py
docker compose exec -T postgres psql -U insidedcpulse -d insidedcpulse -c \
  "SELECT type, count(*) FROM graph_nodes GROUP BY type ORDER BY type;"
```

Expected: identical counts to Step 2 (TRUNCATE + replay reproduces the same graph).

- [ ] **Step 4: Confirm zero behavior change to existing surfaces**

```bash
curl -sf https://insidedcpulse.example/healthz   # replace with actual host from project memory
curl -sf https://insidedcpulse.example/api/v1/world/state | head -c 300
```

(Use the actual production host from `project_insidedcpulse` memory.) Expected: both succeed exactly as before — `world_state`, REST API, and MCP server are unaffected by this change (Sub-project A adds new tables and a new worker call only; no existing endpoint or response shape changes).

---

## Self-review notes (writing-plans skill)

- **Spec coverage:** Migration (Task 1), `parse_entity_ref`/upsert helpers/`project_event` (Tasks 2-4), R1-R3 (Tasks 5-7), worker integration (Task 8), `rebuild_graph_projection.py` + replay determinism (Task 9), live verification (Task 10) — all bullets of the spec's "Sub-project A scope" are covered. Test coverage list from the spec (node/edge creation per op type, R1, R2 positive+no-match, R3, idempotency, replay determinism) is covered across Tasks 2-9; idempotency is covered structurally (all writes go through `UPSERT_*_SQL`/`ENSURE_NODE_SQL` with `ON CONFLICT`, asserted via SQL-constant equality in every upsert test) plus behaviorally in Task 9's `FakeConn` (re-running `project_event` for the same event would overwrite, not duplicate, dict entries).
- **Deviations from spec, called out explicitly:**
  - `project_event`'s event-node `metadata` is `{"event_type": payload.event_type}` (no `score`) — the spec's worker-integration call signature doesn't pass `score` to `project_event`, so it can't appear in metadata without a signature change. Score remains recorded in the `events` table itself.
  - AFFECTED edges additionally carry `metadata.fields = {<field>: <after-value>, ...}` (the spec only specified `weight` = count). This is additive and required for R2/R3 to determine *what* an event set on an entity without re-deriving diffs from the `events` table — falls within "metadata JSONB ... rule_id, evidence event ids, etc." in the storage schema.
  - Test strategy deviates from a real-Postgres integration suite: per `README.md`'s existing "no real Postgres/Redis needed" convention, all unit tests use `AsyncMock`; only the replay-determinism test (Task 9) uses a small hand-written `FakeConn` covering the fixed set of SQL shapes `graph_projection.py` issues.
- **Placeholder scan:** no TBD/TODO/"add error handling"-style steps; every step has runnable code and an exact pytest command with expected outcome.
- **Type consistency:** `project_event(conn, event_db_id, agent_id, payload, applied)` signature is identical across Tasks 3-9 and the worker call in Task 8. `CausalEdge(source, target, confidence, metadata)` is used consistently by R1/R2/R3 and by `project_event`'s `_causal_edges` consumer. `EntityRef(entity, entity_id)` matches `parse_entity_ref`'s return type used in Task 5. `CAUSAL_RULES` is built incrementally (Task 3: `[]`, Task 5: `+R1`, Task 6: `+R2`, Task 7: `+R3`) with each task replacing the prior list literal exactly.
