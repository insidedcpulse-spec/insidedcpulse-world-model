# Graph Query API + MCP Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Graph Query API over the existing `graph_nodes`/`graph_edges` projection — 5 query functions, 5 REST endpoints under `/api/v1/graph`, and 5 MCP tools — per `docs/superpowers/specs/2026-06-14-graph-query-api-mcp-design.md`.

**Architecture:** New pure async query module `backend/app/graph_queries.py` (pool -> Pydantic models) is the single source of truth; `backend/app/routers/graph.py` (REST) and 5 new `@mcp.tool()`s in `backend/app/mcp_server.py` are thin wrappers over it. Nothing writes to `graph_nodes`/`graph_edges` (unchanged: only `projections/graph_projection.project_event()`).

**Tech Stack:** Python 3.12, FastAPI, asyncpg, Pydantic v2, `mcp` (FastMCP), pytest + pytest-asyncio + unittest.mock (no real Postgres/Redis in tests).

All commands below assume `cd /root/insidedcpulse-world-model/backend` and use `.venv/bin/python -m pytest`.

---

### Task 1: Graph response schemas

**Files:**
- Modify: `backend/app/schemas.py` (append at end of file)
- Test: `backend/tests/test_graph_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime, timezone

from app.schemas import (
    CausalChainEntry,
    CausalChainResponse,
    GraphEdge,
    GraphNode,
    GraphNodeDetail,
    NeighborEntry,
    NeighborsResponse,
    PathResponse,
    RelatedEntitiesResponse,
    RelatedEntity,
    TimelineEntry,
    TimelineResponse,
)


def test_graph_schema_models_construct():
    now = datetime.now(timezone.utc)
    node = GraphNode(id="service.checkout", type="service", label="service.checkout",
                      metadata={}, created_at=now, updated_at=now)
    edge = GraphEdge(source_node="incident.inc3", target_node="service.checkout", edge_type="REFERENCES",
                      weight=1.0, metadata={}, source_event_id=42, created_at=now)

    detail = GraphNodeDetail(node=node, edges_out=[edge], edges_in=[])
    assert detail.node.id == "service.checkout"
    assert detail.edges_out[0].edge_type == "REFERENCES"

    neighbor = NeighborEntry(node=node, edge=edge, direction="out")
    neighbors = NeighborsResponse(node_id="incident.inc3", edge_type=None, direction="both", neighbors=[neighbor])
    assert neighbors.neighbors[0].direction == "out"

    path = PathResponse(from_id="a", to_id="b", found=False, path=[], edges=[], depth=0)
    assert path.found is False

    entry = TimelineEntry(event_id="event.42", label="x", metadata={}, weight=1.0,
                           edge_metadata={}, source_event_id=42, created_at=now)
    timeline = TimelineResponse(entity=None, events=[entry])
    assert timeline.events[0].event_id == "event.42"

    causal_entry = CausalChainEntry(depth=1, source_node="alert.a1", target_node="incident.inc3",
                                     weight=0.63, metadata={"rule_id": "alert_precedes_incident"}, source_event_id=40)
    chain = CausalChainResponse(node_id="incident.inc3", direction="upstream", chain=[causal_entry])
    assert chain.chain[0].depth == 1

    related = RelatedEntity(node=node, distance=1, edge_type="OWNED_BY")
    related_resp = RelatedEntitiesResponse(node_id="service.checkout", max_depth=2, related=[related])
    assert related_resp.related[0].distance == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_graph_schemas.py -v`
Expected: FAIL with `ImportError: cannot import name 'GraphNode' from 'app.schemas'`

- [ ] **Step 3: Append the new models to `backend/app/schemas.py`**

```python
class GraphNode(BaseModel):
    id: str
    type: str
    label: str
    metadata: dict
    created_at: datetime
    updated_at: datetime


class GraphEdge(BaseModel):
    source_node: str
    target_node: str
    edge_type: str
    weight: float
    metadata: dict
    source_event_id: int | None
    created_at: datetime


class GraphNodeDetail(BaseModel):
    node: GraphNode
    edges_out: list[GraphEdge]
    edges_in: list[GraphEdge]


class NeighborEntry(BaseModel):
    node: GraphNode
    edge: GraphEdge
    direction: Literal["out", "in"]


class NeighborsResponse(BaseModel):
    node_id: str
    edge_type: str | None
    direction: Literal["out", "in", "both"]
    neighbors: list[NeighborEntry]


class PathResponse(BaseModel):
    from_id: str
    to_id: str
    found: bool
    path: list[str]
    edges: list[GraphEdge]
    depth: int


class TimelineEntry(BaseModel):
    event_id: str
    label: str
    metadata: dict
    weight: float | None = None
    edge_metadata: dict | None = None
    source_event_id: int | None = None
    created_at: datetime


class TimelineResponse(BaseModel):
    entity: str | None
    events: list[TimelineEntry]


class CausalChainEntry(BaseModel):
    depth: int
    source_node: str
    target_node: str
    weight: float
    metadata: dict
    source_event_id: int | None


class CausalChainResponse(BaseModel):
    node_id: str
    direction: Literal["upstream", "downstream"]
    chain: list[CausalChainEntry]


class RelatedEntity(BaseModel):
    node: GraphNode
    distance: int
    edge_type: str


class RelatedEntitiesResponse(BaseModel):
    node_id: str
    max_depth: int
    related: list[RelatedEntity]
```

`Literal` is already imported at the top of `schemas.py` (`from typing import Any, Literal`) — no import changes needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_graph_schemas.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py backend/tests/test_graph_schemas.py
git commit -m "feat: add graph query response schemas"
```

---

### Task 2: `graph_queries.py` — `get_node`

**Files:**
- Create: `backend/app/graph_queries.py`
- Test: `backend/tests/test_graph_queries.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.graph_queries import get_node

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


def _node_row(node_id="incident.inc3", node_type="incident", label="incident.inc3"):
    return {"id": node_id, "type": node_type, "label": label, "metadata": {}, "created_at": NOW, "updated_at": NOW}


def _edge_row(source="incident.inc3", target="service.checkout", edge_type="REFERENCES"):
    return {
        "source_node": source, "target_node": target, "edge_type": edge_type,
        "weight": 1.0, "metadata": {}, "source_event_id": 42, "created_at": NOW,
    }


@pytest.mark.asyncio
async def test_get_node_found():
    pool = AsyncMock()
    pool.fetchrow.return_value = _node_row()
    pool.fetch.side_effect = [[_edge_row()], []]

    result = await get_node(pool, "incident.inc3")

    assert result.node.id == "incident.inc3"
    assert result.node.type == "incident"
    assert len(result.edges_out) == 1
    assert result.edges_out[0].target_node == "service.checkout"
    assert result.edges_in == []


@pytest.mark.asyncio
async def test_get_node_not_found():
    pool = AsyncMock()
    pool.fetchrow.return_value = None

    result = await get_node(pool, "service.nonexistent")

    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_graph_queries.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.graph_queries'`

- [ ] **Step 3: Create `backend/app/graph_queries.py`**

```python
import asyncpg

from app.schemas import (
    CausalChainEntry,
    CausalChainResponse,
    GraphEdge,
    GraphNode,
    GraphNodeDetail,
    NeighborEntry,
    NeighborsResponse,
    PathResponse,
    RelatedEntitiesResponse,
    RelatedEntity,
    TimelineEntry,
    TimelineResponse,
)

MAX_PATH_DEPTH = 10
MAX_CAUSAL_DEPTH = 10
MAX_RELATED_DEPTH = 5


async def _get_raw_node(pool: asyncpg.Pool, node_id: str):
    return await pool.fetchrow(
        "SELECT id, type, label, metadata, created_at, updated_at FROM graph_nodes WHERE id = $1",
        node_id,
    )


async def get_node(pool: asyncpg.Pool, node_id: str) -> GraphNodeDetail | None:
    row = await _get_raw_node(pool, node_id)
    if row is None:
        return None
    edges_out = await pool.fetch(
        "SELECT source_node, target_node, edge_type, weight, metadata, source_event_id, created_at "
        "FROM graph_edges WHERE source_node = $1 ORDER BY edge_type, target_node",
        node_id,
    )
    edges_in = await pool.fetch(
        "SELECT source_node, target_node, edge_type, weight, metadata, source_event_id, created_at "
        "FROM graph_edges WHERE target_node = $1 ORDER BY edge_type, source_node",
        node_id,
    )
    return GraphNodeDetail(
        node=GraphNode(**row),
        edges_out=[GraphEdge(**e) for e in edges_out],
        edges_in=[GraphEdge(**e) for e in edges_in],
    )
```

The unused imports (`CausalChainEntry`, `TimelineEntry`, etc.) are needed by
functions added in later tasks of this same module — left in place now to
avoid repeated import-block edits.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_graph_queries.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/graph_queries.py backend/tests/test_graph_queries.py
git commit -m "feat: add graph_queries.get_node"
```

---

### Task 3: `graph_queries.py` — `get_neighbors`

**Files:**
- Modify: `backend/app/graph_queries.py` (append)
- Test: `backend/tests/test_graph_queries.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_graph_queries.py`:

```python
from app.graph_queries import get_neighbors


def _neighbor_row(source, target, edge_type, n_id, n_type):
    row = _edge_row(source, target, edge_type)
    row.update({
        "n_id": n_id, "n_type": n_type, "n_label": n_id,
        "n_metadata": {}, "n_created_at": NOW, "n_updated_at": NOW,
    })
    return row


@pytest.mark.asyncio
async def test_get_neighbors_out():
    pool = AsyncMock()
    pool.fetchrow.return_value = _node_row("service.checkout", "service", "service.checkout")
    pool.fetch.return_value = [_neighbor_row("service.checkout", "team.sre", "OWNED_BY", "team.sre", "team")]

    result = await get_neighbors(pool, "service.checkout", None, "out", 50)

    assert result.node_id == "service.checkout"
    assert len(result.neighbors) == 1
    assert result.neighbors[0].direction == "out"
    assert result.neighbors[0].node.id == "team.sre"
    assert result.neighbors[0].edge.edge_type == "OWNED_BY"
    pool.fetch.assert_called_once()


@pytest.mark.asyncio
async def test_get_neighbors_both_directions():
    pool = AsyncMock()
    pool.fetchrow.return_value = _node_row("service.checkout", "service", "service.checkout")
    pool.fetch.side_effect = [
        [_neighbor_row("service.checkout", "team.sre", "OWNED_BY", "team.sre", "team")],
        [_neighbor_row("incident.inc3", "service.checkout", "REFERENCES", "incident.inc3", "incident")],
    ]

    result = await get_neighbors(pool, "service.checkout", None, "both", 50)

    assert [n.direction for n in result.neighbors] == ["out", "in"]
    assert result.neighbors[1].node.id == "incident.inc3"
    assert pool.fetch.call_count == 2


@pytest.mark.asyncio
async def test_get_neighbors_limit_skips_in_query_when_exhausted():
    pool = AsyncMock()
    pool.fetchrow.return_value = _node_row("service.checkout", "service", "service.checkout")
    pool.fetch.return_value = [_neighbor_row("service.checkout", "team.sre", "OWNED_BY", "team.sre", "team")]

    result = await get_neighbors(pool, "service.checkout", None, "both", 1)

    assert len(result.neighbors) == 1
    pool.fetch.assert_called_once()


@pytest.mark.asyncio
async def test_get_neighbors_edge_type_filter_passed_through():
    pool = AsyncMock()
    pool.fetchrow.return_value = _node_row("service.checkout", "service", "service.checkout")
    pool.fetch.return_value = []

    await get_neighbors(pool, "service.checkout", "OWNED_BY", "out", 50)

    args, _ = pool.fetch.call_args
    assert args[2] == "OWNED_BY"


@pytest.mark.asyncio
async def test_get_neighbors_not_found():
    pool = AsyncMock()
    pool.fetchrow.return_value = None

    result = await get_neighbors(pool, "service.ghost", None, "both", 50)

    assert result is None
    pool.fetch.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_graph_queries.py -v -k neighbors`
Expected: FAIL with `ImportError: cannot import name 'get_neighbors' from 'app.graph_queries'`

- [ ] **Step 3: Append `_neighbor_entry` + `get_neighbors` to `backend/app/graph_queries.py`**

```python
def _neighbor_entry(row, direction: str) -> NeighborEntry:
    return NeighborEntry(
        node=GraphNode(
            id=row["n_id"], type=row["n_type"], label=row["n_label"],
            metadata=row["n_metadata"], created_at=row["n_created_at"], updated_at=row["n_updated_at"],
        ),
        edge=GraphEdge(
            source_node=row["source_node"], target_node=row["target_node"], edge_type=row["edge_type"],
            weight=row["weight"], metadata=row["metadata"], source_event_id=row["source_event_id"],
            created_at=row["created_at"],
        ),
        direction=direction,
    )


async def get_neighbors(
    pool: asyncpg.Pool, node_id: str, edge_type: str | None, direction: str, limit: int
) -> NeighborsResponse | None:
    """direction: 'out' | 'in' | 'both'."""
    if await _get_raw_node(pool, node_id) is None:
        return None

    neighbors: list[NeighborEntry] = []
    if direction in ("out", "both"):
        rows = await pool.fetch(
            "SELECT e.source_node, e.target_node, e.edge_type, e.weight, e.metadata, e.source_event_id, e.created_at, "
            "n.id AS n_id, n.type AS n_type, n.label AS n_label, n.metadata AS n_metadata, "
            "n.created_at AS n_created_at, n.updated_at AS n_updated_at "
            "FROM graph_edges e JOIN graph_nodes n ON n.id = e.target_node "
            "WHERE e.source_node = $1 AND ($2::text IS NULL OR e.edge_type = $2) "
            "ORDER BY e.edge_type, e.target_node LIMIT $3",
            node_id, edge_type, limit,
        )
        neighbors += [_neighbor_entry(r, "out") for r in rows]
    if direction in ("in", "both"):
        remaining = limit - len(neighbors)
        if remaining > 0:
            rows = await pool.fetch(
                "SELECT e.source_node, e.target_node, e.edge_type, e.weight, e.metadata, e.source_event_id, e.created_at, "
                "n.id AS n_id, n.type AS n_type, n.label AS n_label, n.metadata AS n_metadata, "
                "n.created_at AS n_created_at, n.updated_at AS n_updated_at "
                "FROM graph_edges e JOIN graph_nodes n ON n.id = e.source_node "
                "WHERE e.target_node = $1 AND ($2::text IS NULL OR e.edge_type = $2) "
                "ORDER BY e.edge_type, e.source_node LIMIT $3",
                node_id, edge_type, remaining,
            )
            neighbors += [_neighbor_entry(r, "in") for r in rows]

    return NeighborsResponse(node_id=node_id, edge_type=edge_type, direction=direction, neighbors=neighbors)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_graph_queries.py -v -k neighbors`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/graph_queries.py backend/tests/test_graph_queries.py
git commit -m "feat: add graph_queries.get_neighbors"
```

---

### Task 4: `graph_queries.py` — `get_path`

**Files:**
- Modify: `backend/app/graph_queries.py` (append)
- Test: `backend/tests/test_graph_queries.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_graph_queries.py`:

```python
from app.graph_queries import get_path


@pytest.mark.asyncio
async def test_get_path_same_node():
    pool = AsyncMock()
    pool.fetchrow.return_value = _node_row("service.checkout", "service", "service.checkout")

    result = await get_path(pool, "service.checkout", "service.checkout", 6)

    assert result.found is True
    assert result.path == ["service.checkout"]
    assert result.edges == []
    assert result.depth == 0


@pytest.mark.asyncio
async def test_get_path_one_hop():
    pool = AsyncMock()
    pool.fetchrow.side_effect = [
        _node_row("incident.inc3", "incident", "incident.inc3"),
        _node_row("service.checkout", "service", "service.checkout"),
    ]
    pool.fetch.return_value = [_edge_row("incident.inc3", "service.checkout", "REFERENCES")]

    result = await get_path(pool, "incident.inc3", "service.checkout", 6)

    assert result.found is True
    assert result.path == ["incident.inc3", "service.checkout"]
    assert [e.edge_type for e in result.edges] == ["REFERENCES"]
    assert result.depth == 1


@pytest.mark.asyncio
async def test_get_path_two_hops():
    pool = AsyncMock()
    pool.fetchrow.side_effect = [
        _node_row("incident.inc3", "incident", "incident.inc3"),
        _node_row("team.sre", "team", "team.sre"),
    ]
    pool.fetch.side_effect = [
        [_edge_row("incident.inc3", "service.checkout", "REFERENCES")],
        [_edge_row("service.checkout", "team.sre", "OWNED_BY")],
    ]

    result = await get_path(pool, "incident.inc3", "team.sre", 6)

    assert result.found is True
    assert result.path == ["incident.inc3", "service.checkout", "team.sre"]
    assert [e.edge_type for e in result.edges] == ["REFERENCES", "OWNED_BY"]
    assert result.depth == 2


@pytest.mark.asyncio
async def test_get_path_not_found_within_depth():
    pool = AsyncMock()
    pool.fetchrow.side_effect = [
        _node_row("incident.inc3", "incident", "incident.inc3"),
        _node_row("team.other", "team", "team.other"),
    ]
    pool.fetch.return_value = []

    result = await get_path(pool, "incident.inc3", "team.other", 6)

    assert result.found is False
    assert result.path == []
    assert result.edges == []
    assert result.depth == 6


@pytest.mark.asyncio
async def test_get_path_node_not_found():
    pool = AsyncMock()
    pool.fetchrow.return_value = None

    result = await get_path(pool, "service.ghost", "service.checkout", 6)

    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_graph_queries.py -v -k get_path`
Expected: FAIL with `ImportError: cannot import name 'get_path' from 'app.graph_queries'`

- [ ] **Step 3: Append `_reconstruct` + `get_path` to `backend/app/graph_queries.py`**

```python
def _reconstruct(from_id: str, to_id: str, parents: dict[str, GraphEdge]) -> dict:
    path = [to_id]
    edges: list[GraphEdge] = []
    current = to_id
    while current != from_id:
        edge = parents[current]
        edges.append(edge)
        current = edge.source_node if edge.target_node == current else edge.target_node
        path.append(current)
    path.reverse()
    edges.reverse()
    return {"path": path, "edges": edges}


async def get_path(pool: asyncpg.Pool, from_id: str, to_id: str, max_depth: int) -> PathResponse | None:
    """Undirected bounded BFS. Caller validates max_depth <= MAX_PATH_DEPTH."""
    if await _get_raw_node(pool, from_id) is None or await _get_raw_node(pool, to_id) is None:
        return None
    if from_id == to_id:
        return PathResponse(from_id=from_id, to_id=to_id, found=True, path=[from_id], edges=[], depth=0)

    visited = {from_id}
    parents: dict[str, GraphEdge] = {}
    frontier = [from_id]
    for depth in range(1, max_depth + 1):
        rows = await pool.fetch(
            "SELECT source_node, target_node, edge_type, weight, metadata, source_event_id, created_at "
            "FROM graph_edges WHERE source_node = ANY($1) OR target_node = ANY($1)",
            frontier,
        )
        next_frontier = []
        for r in rows:
            edge = GraphEdge(**r)
            for a, b in ((edge.source_node, edge.target_node), (edge.target_node, edge.source_node)):
                if a in visited and b not in visited:
                    visited.add(b)
                    parents[b] = edge
                    next_frontier.append(b)
                    if b == to_id:
                        return PathResponse(
                            from_id=from_id, to_id=to_id, found=True,
                            **_reconstruct(from_id, to_id, parents), depth=depth,
                        )
        if not next_frontier:
            break
        frontier = next_frontier

    return PathResponse(from_id=from_id, to_id=to_id, found=False, path=[], edges=[], depth=max_depth)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_graph_queries.py -v -k get_path`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/graph_queries.py backend/tests/test_graph_queries.py
git commit -m "feat: add graph_queries.get_path"
```

---

### Task 5: `graph_queries.py` — `get_timeline`

**Files:**
- Modify: `backend/app/graph_queries.py` (append)
- Test: `backend/tests/test_graph_queries.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_graph_queries.py`:

```python
from app.graph_queries import get_timeline


@pytest.mark.asyncio
async def test_get_timeline_for_entity():
    pool = AsyncMock()
    pool.fetchrow.return_value = _node_row("incident.inc3", "incident", "incident.inc3")
    pool.fetch.return_value = [{
        "event_id": "event.42", "label": "mark incident open", "metadata": {"event_type": "vision"},
        "weight": 1.0, "edge_metadata": {"fields": {"status": "open"}}, "source_event_id": 42, "created_at": NOW,
    }]

    result = await get_timeline(pool, "incident.inc3", 50, 0)

    assert result.entity == "incident.inc3"
    assert len(result.events) == 1
    assert result.events[0].event_id == "event.42"
    assert result.events[0].edge_metadata == {"fields": {"status": "open"}}


@pytest.mark.asyncio
async def test_get_timeline_global():
    pool = AsyncMock()
    pool.fetch.return_value = [{
        "event_id": "event.43", "label": "deploy checkout v2", "metadata": {"event_type": "action"},
        "weight": None, "edge_metadata": {}, "source_event_id": None, "created_at": NOW,
    }]

    result = await get_timeline(pool, None, 50, 0)

    assert result.entity is None
    assert result.events[0].event_id == "event.43"
    pool.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_get_timeline_entity_not_found():
    pool = AsyncMock()
    pool.fetchrow.return_value = None

    result = await get_timeline(pool, "service.ghost", 50, 0)

    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_graph_queries.py -v -k timeline`
Expected: FAIL with `ImportError: cannot import name 'get_timeline' from 'app.graph_queries'`

- [ ] **Step 3: Append `get_timeline` to `backend/app/graph_queries.py`**

```python
async def get_timeline(
    pool: asyncpg.Pool, entity: str | None, limit: int, offset: int
) -> TimelineResponse | None:
    if entity is not None:
        if await _get_raw_node(pool, entity) is None:
            return None
        rows = await pool.fetch(
            "SELECT e.source_node AS event_id, n.label, n.metadata, e.weight, "
            "e.metadata AS edge_metadata, e.source_event_id, e.created_at "
            "FROM graph_edges e JOIN graph_nodes n ON n.id = e.source_node "
            "WHERE e.target_node = $1 AND e.edge_type = 'AFFECTED' "
            "ORDER BY e.source_event_id DESC LIMIT $2 OFFSET $3",
            entity, limit, offset,
        )
    else:
        rows = await pool.fetch(
            "SELECT id AS event_id, label, metadata, NULL::numeric AS weight, "
            "'{}'::jsonb AS edge_metadata, NULL::bigint AS source_event_id, created_at "
            "FROM graph_nodes WHERE type = 'event' "
            "ORDER BY split_part(id, '.', 2)::bigint DESC LIMIT $1 OFFSET $2",
            limit, offset,
        )
    return TimelineResponse(entity=entity, events=[TimelineEntry(**r) for r in rows])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_graph_queries.py -v -k timeline`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/graph_queries.py backend/tests/test_graph_queries.py
git commit -m "feat: add graph_queries.get_timeline"
```

---

### Task 6: `graph_queries.py` — `get_causal_edges`

**Files:**
- Modify: `backend/app/graph_queries.py` (append)
- Test: `backend/tests/test_graph_queries.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_graph_queries.py`:

```python
from app.graph_queries import get_causal_edges


@pytest.mark.asyncio
async def test_get_causal_edges_upstream():
    pool = AsyncMock()
    pool.fetchrow.return_value = _node_row("incident.inc3", "incident", "incident.inc3")
    pool.fetch.side_effect = [
        [{"source_node": "alert.a1", "target_node": "incident.inc3", "weight": 0.63,
          "metadata": {"rule_id": "alert_precedes_incident"}, "source_event_id": 40}],
        [],
    ]

    result = await get_causal_edges(pool, "incident.inc3", "upstream", 3)

    assert result.node_id == "incident.inc3"
    assert result.direction == "upstream"
    assert len(result.chain) == 1
    assert result.chain[0].depth == 1
    assert result.chain[0].source_node == "alert.a1"


@pytest.mark.asyncio
async def test_get_causal_edges_downstream_multi_depth():
    pool = AsyncMock()
    pool.fetchrow.return_value = _node_row("deployment.checkout_v2", "deployment", "deployment.checkout_v2")
    pool.fetch.side_effect = [
        [{"source_node": "deployment.checkout_v2", "target_node": "service.checkout", "weight": 0.7,
          "metadata": {"rule_id": "deployment_precedes_degradation"}, "source_event_id": 50}],
        [{"source_node": "service.checkout", "target_node": "incident.inc4", "weight": 0.6,
          "metadata": {"rule_id": "alert_precedes_incident"}, "source_event_id": 51}],
        [],
    ]

    result = await get_causal_edges(pool, "deployment.checkout_v2", "downstream", 3)

    assert [c.depth for c in result.chain] == [1, 2]
    assert result.chain[1].target_node == "incident.inc4"


@pytest.mark.asyncio
async def test_get_causal_edges_empty_chain():
    pool = AsyncMock()
    pool.fetchrow.return_value = _node_row("region.eu", "region", "region.eu")
    pool.fetch.return_value = []

    result = await get_causal_edges(pool, "region.eu", "upstream", 3)

    assert result.chain == []


@pytest.mark.asyncio
async def test_get_causal_edges_cycle_terminates():
    pool = AsyncMock()
    pool.fetchrow.return_value = _node_row("incident.a", "incident", "incident.a")
    pool.fetch.side_effect = [
        [{"source_node": "incident.a", "target_node": "incident.b", "weight": 1.0, "metadata": {}, "source_event_id": 1}],
        [{"source_node": "incident.b", "target_node": "incident.a", "weight": 1.0, "metadata": {}, "source_event_id": 2}],
    ]

    result = await get_causal_edges(pool, "incident.a", "downstream", 5)

    assert len(result.chain) == 2
    assert pool.fetch.call_count == 2


@pytest.mark.asyncio
async def test_get_causal_edges_not_found():
    pool = AsyncMock()
    pool.fetchrow.return_value = None

    result = await get_causal_edges(pool, "service.ghost", "upstream", 3)

    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_graph_queries.py -v -k causal`
Expected: FAIL with `ImportError: cannot import name 'get_causal_edges' from 'app.graph_queries'`

- [ ] **Step 3: Append `get_causal_edges` to `backend/app/graph_queries.py`**

```python
async def get_causal_edges(
    pool: asyncpg.Pool, node_id: str, direction: str, max_depth: int
) -> CausalChainResponse | None:
    """direction: 'upstream' (what caused node_id) | 'downstream' (what node_id caused)."""
    if await _get_raw_node(pool, node_id) is None:
        return None

    visited = {node_id}
    frontier = [node_id]
    chain: list[CausalChainEntry] = []
    for depth in range(1, max_depth + 1):
        if direction == "upstream":
            rows = await pool.fetch(
                "SELECT source_node, target_node, weight, metadata, source_event_id "
                "FROM graph_edges WHERE edge_type = 'CAUSED' AND target_node = ANY($1)",
                frontier,
            )
            other_key = "source_node"
        else:
            rows = await pool.fetch(
                "SELECT source_node, target_node, weight, metadata, source_event_id "
                "FROM graph_edges WHERE edge_type = 'CAUSED' AND source_node = ANY($1)",
                frontier,
            )
            other_key = "target_node"

        next_frontier = []
        for r in rows:
            chain.append(CausalChainEntry(depth=depth, **r))
            other = r[other_key]
            if other not in visited:
                visited.add(other)
                next_frontier.append(other)
        if not next_frontier:
            break
        frontier = next_frontier

    return CausalChainResponse(node_id=node_id, direction=direction, chain=chain)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_graph_queries.py -v -k causal`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/graph_queries.py backend/tests/test_graph_queries.py
git commit -m "feat: add graph_queries.get_causal_edges"
```

---

### Task 7: `graph_queries.py` — `find_related`

**Files:**
- Modify: `backend/app/graph_queries.py` (append)
- Test: `backend/tests/test_graph_queries.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_graph_queries.py`:

```python
from app.graph_queries import find_related


@pytest.mark.asyncio
async def test_find_related_both_directions():
    pool = AsyncMock()
    pool.fetchrow.side_effect = [
        _node_row("service.checkout", "service", "service.checkout"),
        _node_row("team.sre", "team", "team.sre"),
    ]
    pool.fetch.side_effect = [
        [_edge_row("service.checkout", "team.sre", "OWNED_BY")],
        [],
    ]

    result = await find_related(pool, "service.checkout", None, "both", 2, 50)

    assert result.node_id == "service.checkout"
    assert len(result.related) == 1
    assert result.related[0].node.id == "team.sre"
    assert result.related[0].distance == 1
    assert result.related[0].edge_type == "OWNED_BY"


@pytest.mark.asyncio
async def test_find_related_edge_types_filter_passed_to_query():
    pool = AsyncMock()
    pool.fetchrow.return_value = _node_row("service.checkout", "service", "service.checkout")
    pool.fetch.return_value = []

    await find_related(pool, "service.checkout", ["OWNED_BY"], "both", 2, 50)

    args, _ = pool.fetch.call_args
    assert args[2] == ["OWNED_BY"]


@pytest.mark.asyncio
async def test_find_related_max_depth_cutoff():
    pool = AsyncMock()
    pool.fetchrow.side_effect = [
        _node_row("incident.inc3", "incident", "incident.inc3"),
        _node_row("service.checkout", "service", "service.checkout"),
    ]
    pool.fetch.return_value = [_edge_row("incident.inc3", "service.checkout", "REFERENCES")]

    result = await find_related(pool, "incident.inc3", None, "both", 1, 50)

    assert len(result.related) == 1
    assert pool.fetch.call_count == 1


@pytest.mark.asyncio
async def test_find_related_limit_cutoff():
    pool = AsyncMock()
    pool.fetchrow.side_effect = [
        _node_row("team.sre", "team", "team.sre"),
        _node_row("service.checkout", "service", "service.checkout"),
    ]
    pool.fetch.return_value = [
        _edge_row("service.checkout", "team.sre", "OWNED_BY"),
        _edge_row("service.auth", "team.sre", "OWNED_BY"),
    ]

    result = await find_related(pool, "team.sre", None, "both", 2, 1)

    assert len(result.related) == 1


@pytest.mark.asyncio
async def test_find_related_not_found():
    pool = AsyncMock()
    pool.fetchrow.return_value = None

    result = await find_related(pool, "service.ghost", None, "both", 2, 50)

    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_graph_queries.py -v -k find_related`
Expected: FAIL with `ImportError: cannot import name 'find_related' from 'app.graph_queries'`

- [ ] **Step 3: Append `find_related` to `backend/app/graph_queries.py`**

```python
async def find_related(
    pool: asyncpg.Pool, node_id: str, edge_types: list[str] | None, direction: str,
    max_depth: int, limit: int,
) -> RelatedEntitiesResponse | None:
    """direction: 'out' | 'in' | 'both'. edge_types=None means any edge type."""
    if await _get_raw_node(pool, node_id) is None:
        return None

    visited = {node_id}
    frontier = [node_id]
    related: list[RelatedEntity] = []
    for depth in range(1, max_depth + 1):
        if len(related) >= limit:
            break
        clauses = []
        if direction in ("out", "both"):
            clauses.append("source_node = ANY($1)")
        if direction in ("in", "both"):
            clauses.append("target_node = ANY($1)")
        rows = await pool.fetch(
            f"SELECT source_node, target_node, edge_type, weight, metadata, source_event_id, created_at "
            f"FROM graph_edges WHERE ({' OR '.join(clauses)}) "
            f"AND ($2::text[] IS NULL OR edge_type = ANY($2))",
            frontier, edge_types,
        )
        next_frontier = []
        for r in rows:
            for a, b in ((r["source_node"], r["target_node"]), (r["target_node"], r["source_node"])):
                if a in frontier and b not in visited and len(related) < limit:
                    visited.add(b)
                    node_row = await _get_raw_node(pool, b)
                    related.append(RelatedEntity(node=GraphNode(**node_row), distance=depth, edge_type=r["edge_type"]))
                    next_frontier.append(b)
        if not next_frontier:
            break
        frontier = next_frontier

    return RelatedEntitiesResponse(node_id=node_id, max_depth=max_depth, related=related)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_graph_queries.py -v -k find_related`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the full query module test file**

Run: `.venv/bin/python -m pytest tests/test_graph_queries.py -v`
Expected: PASS (25 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/app/graph_queries.py backend/tests/test_graph_queries.py
git commit -m "feat: add graph_queries.find_related"
```

---

### Task 8: REST router `backend/app/routers/graph.py`

**Files:**
- Create: `backend/app/routers/graph.py`
- Test: `backend/tests/test_graph_router.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_graph_router.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.routers.graph import causal_chain, neighbors, node, path, timeline

AGENT = {"id": "agent-1"}


@pytest.mark.asyncio
async def test_node_found():
    fake = MagicMock()
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_node", AsyncMock(return_value=fake)):
        result = await node("service.checkout", agent=AGENT)

    assert result is fake


@pytest.mark.asyncio
async def test_node_not_found():
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_node", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await node("service.ghost", agent=AGENT)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_neighbors_found():
    fake = MagicMock()
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_neighbors", AsyncMock(return_value=fake)) as mock_fn:
        result = await neighbors("service.checkout", None, "both", 50, agent=AGENT)

    assert result is fake
    assert mock_fn.await_args.args[1:] == ("service.checkout", None, "both", 50)


@pytest.mark.asyncio
async def test_neighbors_not_found():
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_neighbors", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await neighbors("service.ghost", None, "both", 50, agent=AGENT)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_path_found():
    fake = MagicMock()
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_path", AsyncMock(return_value=fake)):
        result = await path(from_="incident.inc3", to="service.checkout", max_depth=6, agent=AGENT)

    assert result is fake


@pytest.mark.asyncio
async def test_path_node_not_found():
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_path", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await path(from_="service.ghost", to="service.checkout", max_depth=6, agent=AGENT)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_timeline_found():
    fake = MagicMock()
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_timeline", AsyncMock(return_value=fake)):
        result = await timeline(entity=None, limit=50, offset=0, agent=AGENT)

    assert result is fake


@pytest.mark.asyncio
async def test_timeline_entity_not_found():
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_timeline", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await timeline(entity="service.ghost", limit=50, offset=0, agent=AGENT)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_causal_chain_found():
    fake = MagicMock()
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_causal_edges", AsyncMock(return_value=fake)):
        result = await causal_chain(node="incident.inc3", direction="upstream", max_depth=3, agent=AGENT)

    assert result is fake


@pytest.mark.asyncio
async def test_causal_chain_not_found():
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_causal_edges", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await causal_chain(node="service.ghost", direction="upstream", max_depth=3, agent=AGENT)

    assert exc_info.value.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_graph_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routers.graph'`

- [ ] **Step 3: Create `backend/app/routers/graph.py`**

```python
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.config import settings
from app.database import get_pool
from app.graph_queries import (
    MAX_CAUSAL_DEPTH,
    MAX_PATH_DEPTH,
    get_causal_edges,
    get_neighbors,
    get_node,
    get_path,
    get_timeline,
)
from app.rate_limit import rate_limited
from app.schemas import CausalChainResponse, GraphNodeDetail, NeighborsResponse, PathResponse, TimelineResponse

router = APIRouter(prefix="/api/v1/graph", tags=["graph"])

READ = settings.rate_limit_read_per_window


@router.get("/node/{node_id}", response_model=GraphNodeDetail)
async def node(node_id: str, agent: dict = Depends(rate_limited(READ))):
    """Node plus its direct outgoing/incoming edges."""
    result = await get_node(get_pool(), node_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"node not found: {node_id}")
    return result


@router.get("/neighbors/{node_id}", response_model=NeighborsResponse)
async def neighbors(
    node_id: str,
    edge_type: str | None = None,
    direction: Literal["out", "in", "both"] = "both",
    limit: int = Query(default=50, ge=1, le=200),
    agent: dict = Depends(rate_limited(READ)),
):
    """Nodes directly connected to node_id, optionally filtered by edge_type/direction."""
    result = await get_neighbors(get_pool(), node_id, edge_type, direction, limit)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"node not found: {node_id}")
    return result


@router.get("/path", response_model=PathResponse)
async def path(
    from_: str = Query(alias="from"),
    to: str = Query(...),
    max_depth: int = Query(default=6, ge=1, le=MAX_PATH_DEPTH),
    agent: dict = Depends(rate_limited(READ)),
):
    """Shortest path (undirected, bounded BFS) between two nodes."""
    result = await get_path(get_pool(), from_, to, max_depth)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "from/to node not found")
    return result


@router.get("/timeline", response_model=TimelineResponse)
async def timeline(
    entity: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    agent: dict = Depends(rate_limited(READ)),
):
    """AFFECTED-edge history for `entity`, or the global event chain if omitted."""
    result = await get_timeline(get_pool(), entity, limit, offset)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"node not found: {entity}")
    return result


@router.get("/causal-chain", response_model=CausalChainResponse)
async def causal_chain(
    node: str = Query(...),
    direction: Literal["upstream", "downstream"] = "upstream",
    max_depth: int = Query(default=3, ge=1, le=MAX_CAUSAL_DEPTH),
    agent: dict = Depends(rate_limited(READ)),
):
    """Traverse CAUSED edges upstream (causes) or downstream (effects)."""
    result = await get_causal_edges(get_pool(), node, direction, max_depth)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"node not found: {node}")
    return result
```

`direction: Literal[...]` and `Query(ge=, le=)` give 422 automatically for
invalid `direction`/out-of-range `max_depth`/`limit` — standard FastAPI
validation, not separately unit-tested here (same as every other endpoint
in `routers/world.py`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_graph_router.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/graph.py backend/tests/test_graph_router.py
git commit -m "feat: add /api/v1/graph REST router"
```

---

### Task 9: Wire the graph router into the app

**Files:**
- Modify: `backend/app/main.py:13` (import) and `:51` (include_router)

- [ ] **Step 1: Update imports and router registration**

In `backend/app/main.py`, change:

```python
from app.routers import agents, world, ws
```

to:

```python
from app.routers import agents, graph, world, ws
```

And change:

```python
app.include_router(world.router)
app.include_router(agents.router)
app.include_router(ws.router)
```

to:

```python
app.include_router(world.router)
app.include_router(agents.router)
app.include_router(graph.router)
app.include_router(ws.router)
```

- [ ] **Step 2: Run the smoke test and MCP mount test to verify nothing broke**

Run: `.venv/bin/python -m pytest tests/test_smoke.py tests/test_mcp_mount.py -v`
Expected: PASS (4 passed)

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: mount /api/v1/graph router in the app"
```

---

### Task 10: MCP tool `get_graph_node`

**Files:**
- Modify: `backend/app/mcp_server.py`
- Test: `backend/tests/test_mcp_server.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mcp_server.py`:

```python
from app.mcp_server import get_graph_node


@pytest.mark.asyncio
async def test_get_graph_node_success():
    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"node": {"id": "incident.inc3"}, "edges_out": [], "edges_in": []}

    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_node", AsyncMock(return_value=fake_result)):
        result = await get_graph_node(api_key="key", node_id="incident.inc3")

    assert result == {"node": {"id": "incident.inc3"}, "edges_out": [], "edges_in": []}


@pytest.mark.asyncio
async def test_get_graph_node_invalid_api_key():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid API key"):
            await get_graph_node(api_key="bad-key", node_id="incident.inc3")


@pytest.mark.asyncio
async def test_get_graph_node_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await get_graph_node(api_key="key", node_id="incident.inc3")


@pytest.mark.asyncio
async def test_get_graph_node_not_found():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_node", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="node not found: service.ghost"):
            await get_graph_node(api_key="key", node_id="service.ghost")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v -k get_graph_node`
Expected: FAIL with `ImportError: cannot import name 'get_graph_node' from 'app.mcp_server'`

- [ ] **Step 3: Add the import and the tool to `backend/app/mcp_server.py`**

Add to the import block near the top (alongside the existing `from app.world_state import get_state, simulate_ops`):

```python
from app.graph_queries import get_node
```

Append at the end of the file:

```python
@mcp.tool()
async def get_graph_node(api_key: str, node_id: str) -> dict:
    """Return a graph node plus its direct outgoing/incoming edges."""
    await _authenticate(api_key, READ)
    result = await get_node(get_pool(), node_id)
    if result is None:
        raise ValueError(f"node not found: {node_id}")
    return result.model_dump(mode="json")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v -k get_graph_node`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/mcp_server.py backend/tests/test_mcp_server.py
git commit -m "feat: add get_graph_node MCP tool"
```

---

### Task 11: MCP tool `get_graph_neighbors`

**Files:**
- Modify: `backend/app/mcp_server.py`
- Test: `backend/tests/test_mcp_server.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mcp_server.py`:

```python
from app.mcp_server import get_graph_neighbors


@pytest.mark.asyncio
async def test_get_graph_neighbors_success():
    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"node_id": "service.checkout", "edge_type": None, "direction": "both", "neighbors": []}

    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_neighbors", AsyncMock(return_value=fake_result)):
        result = await get_graph_neighbors(api_key="key", node_id="service.checkout")

    assert result["node_id"] == "service.checkout"


@pytest.mark.asyncio
async def test_get_graph_neighbors_invalid_api_key():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid API key"):
            await get_graph_neighbors(api_key="bad-key", node_id="service.checkout")


@pytest.mark.asyncio
async def test_get_graph_neighbors_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await get_graph_neighbors(api_key="key", node_id="service.checkout")


@pytest.mark.asyncio
async def test_get_graph_neighbors_not_found():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_neighbors", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="node not found: service.ghost"):
            await get_graph_neighbors(api_key="key", node_id="service.ghost")


@pytest.mark.asyncio
async def test_get_graph_neighbors_invalid_direction():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid direction"):
            await get_graph_neighbors(api_key="key", node_id="service.checkout", direction="sideways")


@pytest.mark.asyncio
async def test_get_graph_neighbors_limit_out_of_range():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="limit must be between 1 and 200"):
            await get_graph_neighbors(api_key="key", node_id="service.checkout", limit=500)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v -k get_graph_neighbors`
Expected: FAIL with `ImportError: cannot import name 'get_graph_neighbors' from 'app.mcp_server'`

- [ ] **Step 3: Add the import and the tool to `backend/app/mcp_server.py`**

Change:

```python
from app.graph_queries import get_node
```

to:

```python
from app.graph_queries import get_neighbors, get_node
```

Append at the end of the file:

```python
@mcp.tool()
async def get_graph_neighbors(
    api_key: str, node_id: str, edge_type: str | None = None, direction: str = "both", limit: int = 50,
) -> dict:
    """List nodes directly connected to node_id, optionally filtered by edge_type. direction: 'out'|'in'|'both'."""
    await _authenticate(api_key, READ)
    if direction not in ("out", "in", "both"):
        raise ValueError(f"invalid direction: {direction}")
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    result = await get_neighbors(get_pool(), node_id, edge_type, direction, limit)
    if result is None:
        raise ValueError(f"node not found: {node_id}")
    return result.model_dump(mode="json")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v -k get_graph_neighbors`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/mcp_server.py backend/tests/test_mcp_server.py
git commit -m "feat: add get_graph_neighbors MCP tool"
```

---

### Task 12: MCP tool `get_event_timeline`

**Files:**
- Modify: `backend/app/mcp_server.py`
- Test: `backend/tests/test_mcp_server.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mcp_server.py`:

```python
from app.mcp_server import get_event_timeline


@pytest.mark.asyncio
async def test_get_event_timeline_success():
    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"entity": None, "events": []}

    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_timeline", AsyncMock(return_value=fake_result)):
        result = await get_event_timeline(api_key="key")

    assert result == {"entity": None, "events": []}


@pytest.mark.asyncio
async def test_get_event_timeline_invalid_api_key():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid API key"):
            await get_event_timeline(api_key="bad-key")


@pytest.mark.asyncio
async def test_get_event_timeline_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await get_event_timeline(api_key="key")


@pytest.mark.asyncio
async def test_get_event_timeline_entity_not_found():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_timeline", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="node not found: service.ghost"):
            await get_event_timeline(api_key="key", entity="service.ghost")


@pytest.mark.asyncio
async def test_get_event_timeline_limit_out_of_range():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="limit must be between 1 and 200"):
            await get_event_timeline(api_key="key", limit=0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v -k get_event_timeline`
Expected: FAIL with `ImportError: cannot import name 'get_event_timeline' from 'app.mcp_server'`

- [ ] **Step 3: Add the import and the tool to `backend/app/mcp_server.py`**

Change:

```python
from app.graph_queries import get_neighbors, get_node
```

to:

```python
from app.graph_queries import get_neighbors, get_node, get_timeline
```

Append at the end of the file:

```python
@mcp.tool()
async def get_event_timeline(api_key: str, entity: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    """Ordered event history: AFFECTED edges for `entity`, or the global event chain if omitted."""
    await _authenticate(api_key, READ)
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    result = await get_timeline(get_pool(), entity, limit, offset)
    if result is None:
        raise ValueError(f"node not found: {entity}")
    return result.model_dump(mode="json")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v -k get_event_timeline`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/mcp_server.py backend/tests/test_mcp_server.py
git commit -m "feat: add get_event_timeline MCP tool"
```

---

### Task 13: MCP tool `get_causal_chain`

**Files:**
- Modify: `backend/app/mcp_server.py`
- Test: `backend/tests/test_mcp_server.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mcp_server.py`:

```python
from app.mcp_server import get_causal_chain


@pytest.mark.asyncio
async def test_get_causal_chain_success():
    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"node_id": "incident.inc3", "direction": "upstream", "chain": []}

    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_causal_edges", AsyncMock(return_value=fake_result)):
        result = await get_causal_chain(api_key="key", node_id="incident.inc3")

    assert result["direction"] == "upstream"


@pytest.mark.asyncio
async def test_get_causal_chain_invalid_api_key():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid API key"):
            await get_causal_chain(api_key="bad-key", node_id="incident.inc3")


@pytest.mark.asyncio
async def test_get_causal_chain_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await get_causal_chain(api_key="key", node_id="incident.inc3")


@pytest.mark.asyncio
async def test_get_causal_chain_not_found():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_causal_edges", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="node not found: service.ghost"):
            await get_causal_chain(api_key="key", node_id="service.ghost")


@pytest.mark.asyncio
async def test_get_causal_chain_invalid_direction():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid direction"):
            await get_causal_chain(api_key="key", node_id="incident.inc3", direction="sideways")


@pytest.mark.asyncio
async def test_get_causal_chain_max_depth_out_of_range():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="max_depth must be between 1 and 10"):
            await get_causal_chain(api_key="key", node_id="incident.inc3", max_depth=11)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v -k get_causal_chain`
Expected: FAIL with `ImportError: cannot import name 'get_causal_chain' from 'app.mcp_server'`

- [ ] **Step 3: Add the import and the tool to `backend/app/mcp_server.py`**

Change:

```python
from app.graph_queries import get_neighbors, get_node, get_timeline
```

to:

```python
from app.graph_queries import MAX_CAUSAL_DEPTH, get_causal_edges, get_neighbors, get_node, get_timeline
```

Append at the end of the file:

```python
@mcp.tool()
async def get_causal_chain(api_key: str, node_id: str, direction: str = "upstream", max_depth: int = 3) -> dict:
    """Traverse CAUSED edges upstream (what caused node_id) or downstream (what node_id caused)."""
    await _authenticate(api_key, READ)
    if direction not in ("upstream", "downstream"):
        raise ValueError(f"invalid direction: {direction}")
    if not 1 <= max_depth <= MAX_CAUSAL_DEPTH:
        raise ValueError(f"max_depth must be between 1 and {MAX_CAUSAL_DEPTH}")
    result = await get_causal_edges(get_pool(), node_id, direction, max_depth)
    if result is None:
        raise ValueError(f"node not found: {node_id}")
    return result.model_dump(mode="json")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v -k get_causal_chain`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/mcp_server.py backend/tests/test_mcp_server.py
git commit -m "feat: add get_causal_chain MCP tool"
```

---

### Task 14: MCP tool `find_related_entities`

**Files:**
- Modify: `backend/app/mcp_server.py`
- Test: `backend/tests/test_mcp_server.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mcp_server.py`:

```python
from app.mcp_server import find_related_entities


@pytest.mark.asyncio
async def test_find_related_entities_success():
    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"node_id": "service.checkout", "max_depth": 2, "related": []}

    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.find_related", AsyncMock(return_value=fake_result)):
        result = await find_related_entities(api_key="key", node_id="service.checkout")

    assert result["max_depth"] == 2


@pytest.mark.asyncio
async def test_find_related_entities_invalid_api_key():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid API key"):
            await find_related_entities(api_key="bad-key", node_id="service.checkout")


@pytest.mark.asyncio
async def test_find_related_entities_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await find_related_entities(api_key="key", node_id="service.checkout")


@pytest.mark.asyncio
async def test_find_related_entities_not_found():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.find_related", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="node not found: service.ghost"):
            await find_related_entities(api_key="key", node_id="service.ghost")


@pytest.mark.asyncio
async def test_find_related_entities_invalid_direction():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid direction"):
            await find_related_entities(api_key="key", node_id="service.checkout", direction="sideways")


@pytest.mark.asyncio
async def test_find_related_entities_max_depth_out_of_range():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="max_depth must be between 1 and 5"):
            await find_related_entities(api_key="key", node_id="service.checkout", max_depth=6)


@pytest.mark.asyncio
async def test_find_related_entities_limit_out_of_range():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="limit must be between 1 and 200"):
            await find_related_entities(api_key="key", node_id="service.checkout", limit=0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v -k find_related_entities`
Expected: FAIL with `ImportError: cannot import name 'find_related_entities' from 'app.mcp_server'`

- [ ] **Step 3: Add the import and the tool to `backend/app/mcp_server.py`**

Change:

```python
from app.graph_queries import MAX_CAUSAL_DEPTH, get_causal_edges, get_neighbors, get_node, get_timeline
```

to:

```python
from app.graph_queries import (
    MAX_CAUSAL_DEPTH,
    MAX_RELATED_DEPTH,
    find_related,
    get_causal_edges,
    get_neighbors,
    get_node,
    get_timeline,
)
```

Append at the end of the file:

```python
@mcp.tool()
async def find_related_entities(
    api_key: str, node_id: str, edge_types: list[str] | None = None, direction: str = "both",
    max_depth: int = 2, limit: int = 50,
) -> dict:
    """BFS from node_id across any/specified edge types, up to max_depth hops. direction: 'out'|'in'|'both'."""
    await _authenticate(api_key, READ)
    if direction not in ("out", "in", "both"):
        raise ValueError(f"invalid direction: {direction}")
    if not 1 <= max_depth <= MAX_RELATED_DEPTH:
        raise ValueError(f"max_depth must be between 1 and {MAX_RELATED_DEPTH}")
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    result = await find_related(get_pool(), node_id, edge_types, direction, max_depth, limit)
    if result is None:
        raise ValueError(f"node not found: {node_id}")
    return result.model_dump(mode="json")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v -k find_related_entities`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/mcp_server.py backend/tests/test_mcp_server.py
git commit -m "feat: add find_related_entities MCP tool"
```

---

### Task 15: Full backend test suite

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run: `.venv/bin/python -m pytest -v`
Expected: all tests pass (existing suite + the new `test_graph_schemas.py`,
`test_graph_queries.py`, `test_graph_router.py`, and the 5 new tools in
`test_mcp_server.py`)

- [ ] **Step 2: If anything fails, fix and re-run before moving on**

No code change expected at this point — this task exists to catch any
cross-task interaction (e.g. import ordering) before considering the
feature done.
