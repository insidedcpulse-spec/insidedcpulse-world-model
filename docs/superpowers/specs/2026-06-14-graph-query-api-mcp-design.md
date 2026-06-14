# Graph Query API + MCP Tools

## Context

`docs/superpowers/specs/2026-06-13-graph-memory-projection-design.md`
("Sub-project A") shipped the Graph Memory Projection: `graph_nodes` /
`graph_edges`, populated atomically (same transaction as `commit_ops`) by
`app/projections/graph_projection.project_event()`, with edge types
`PROPOSED`, `AFFECTED`, `PRECEDES`, `REFERENCES`, `OWNED_BY`, `CAUSED`
(R1-R3). That spec sketched "Sub-project B — Graph Query API + MCP tools"
at a high level but did not implement it. This design formalizes Sub-project
B to implementation-ready detail, in the style of
`2026-06-10-mcp-server-design.md`.

The graph projection is read-only from this design's perspective: nothing
here writes to `graph_nodes`/`graph_edges`. This adds a query layer on top,
exposed both as REST (`/api/v1/graph/*`) and as 5 new read-only MCP tools.

## Architecture

```
REST client                          LLM (MCP)
  |                                     |
GET /api/v1/graph/*                @mcp.tool()(api_key, ...)
  | Depends(rate_limited(READ))        | _authenticate(api_key, READ)
  +------------------+------------------+
                      v
        backend/app/graph_queries.py (NEW)
        pure async functions, pool -> Pydantic models
                      v
        graph_nodes / graph_edges (Postgres)
        written only by projections/graph_projection.project_event()
```

Same shape as the existing MCP server: REST router and MCP tools are thin
wrappers around shared query functions (`graph_queries.py`), no duplicated
SQL — mirrors how `world.py` / `mcp_server.py` both call into
`world_state.py`.

**Asymmetric tool surface (deliberate):** REST exposes 5 endpoints
(`node`, `neighbors`, `path`, `timeline`, `causal-chain`). MCP exposes a
different 5th tool (`find_related_entities` instead of `path`), because
`path` requires both `from` and `to` node ids — an LLM agent investigating
"what's going on with `incident.inc3`" rarely knows the second id in
advance. `find_related_entities` (BFS from a single node, no target needed)
is the agent-ergonomic equivalent. `path` stays REST-only (debugging /
dashboards); `find_related` stays MCP-only (no REST endpoint) — avoids
growing the REST surface for a capability whose only consumer today is the
MCP tool. Both REST and MCP `path`-like surfaces are independently capped.

## Components

### 1. `backend/app/graph_queries.py` (new)

Pure async query functions, each takes `pool` (+ params) and returns a
Pydantic model (or `None` if the root node doesn't exist, mapped by callers
to 404 / `ValueError`).

```python
import asyncpg

from app.schemas import (
    CausalChainEntry, CausalChainResponse, GraphEdge, GraphNode,
    GraphNodeDetail, NeighborEntry, NeighborsResponse, PathResponse,
    RelatedEntitiesResponse, RelatedEntity, TimelineEntry, TimelineResponse,
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


async def get_neighbors(
    pool: asyncpg.Pool, node_id: str, edge_type: str | None, direction: str, limit: int
) -> NeighborsResponse | None:
    """direction: 'out' | 'in' | 'both'."""
    if await _get_raw_node(pool, node_id) is None:
        return None

    neighbors: list[NeighborEntry] = []
    if direction in ("out", "both"):
        rows = await pool.fetch(
            "SELECT e.*, n.id AS n_id, n.type AS n_type, n.label AS n_label, "
            "n.metadata AS n_metadata, n.created_at AS n_created_at, n.updated_at AS n_updated_at "
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
                "SELECT e.*, n.id AS n_id, n.type AS n_type, n.label AS n_label, "
                "n.metadata AS n_metadata, n.created_at AS n_created_at, n.updated_at AS n_updated_at "
                "FROM graph_edges e JOIN graph_nodes n ON n.id = e.source_node "
                "WHERE e.target_node = $1 AND ($2::text IS NULL OR e.edge_type = $2) "
                "ORDER BY e.edge_type, e.source_node LIMIT $3",
                node_id, edge_type, remaining,
            )
            neighbors += [_neighbor_entry(r, "in") for r in rows]

    return NeighborsResponse(node_id=node_id, edge_type=edge_type, direction=direction, neighbors=neighbors)


async def get_path(pool: asyncpg.Pool, from_id: str, to_id: str, max_depth: int) -> PathResponse | None:
    """Undirected bounded BFS. max_depth validated by caller against MAX_PATH_DEPTH."""
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
            next_frontier_key = "source_node"
        else:
            rows = await pool.fetch(
                "SELECT source_node, target_node, weight, metadata, source_event_id "
                "FROM graph_edges WHERE edge_type = 'CAUSED' AND source_node = ANY($1)",
                frontier,
            )
            next_frontier_key = "target_node"

        next_frontier = []
        for r in rows:
            other = r[next_frontier_key]
            chain.append(CausalChainEntry(depth=depth, **r))
            if other not in visited:
                visited.add(other)
                next_frontier.append(other)
        if not next_frontier:
            break
        frontier = next_frontier

    return CausalChainResponse(node_id=node_id, direction=direction, chain=chain)


async def find_related(
    pool: asyncpg.Pool, node_id: str, edge_types: list[str] | None, direction: str,
    max_depth: int, limit: int,
) -> RelatedEntitiesResponse | None:
    """Undirected (or 'out'/'in') BFS across edge_types (None = any), up to max_depth hops."""
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
            clauses.append("(source_node = ANY($1))")
        if direction in ("in", "both"):
            clauses.append("(target_node = ANY($1))")
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

`_neighbor_entry` and `_reconstruct` are small private helpers (row ->
`NeighborEntry`, and walking `parents` backwards from `to_id` to `from_id`
to build `path`/`edges` in forward order) — straightforward, omitted here.

### 2. `backend/app/schemas.py` (extend)

```python
from typing import Literal


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

### 3. `backend/app/routers/graph.py` (new)

```python
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.config import settings
from app.database import get_pool
from app.graph_queries import (
    MAX_CAUSAL_DEPTH, MAX_PATH_DEPTH, get_causal_edges, get_neighbors, get_node, get_path, get_timeline,
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

### 4. `backend/app/mcp_server.py` (extend)

```python
from app.graph_queries import (
    MAX_CAUSAL_DEPTH, MAX_RELATED_DEPTH, find_related, get_causal_edges, get_neighbors, get_node, get_timeline,
)


@mcp.tool()
async def get_graph_node(api_key: str, node_id: str) -> dict:
    """Return a graph node plus its direct outgoing/incoming edges."""
    await _authenticate(api_key, READ)
    result = await get_node(get_pool(), node_id)
    if result is None:
        raise ValueError(f"node not found: {node_id}")
    return result.model_dump(mode="json")


@mcp.tool()
async def get_graph_neighbors(
    api_key: str, node_id: str, edge_type: str | None = None, direction: str = "both", limit: int = 50,
) -> dict:
    """List nodes directly connected to node_id, optionally filtered by edge_type/direction ('out'|'in'|'both')."""
    await _authenticate(api_key, READ)
    if direction not in ("out", "in", "both"):
        raise ValueError(f"invalid direction: {direction}")
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    result = await get_neighbors(get_pool(), node_id, edge_type, direction, limit)
    if result is None:
        raise ValueError(f"node not found: {node_id}")
    return result.model_dump(mode="json")


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


@mcp.tool()
async def find_related_entities(
    api_key: str, node_id: str, edge_types: list[str] | None = None, direction: str = "both",
    max_depth: int = 2, limit: int = 50,
) -> dict:
    """BFS from node_id across any/specified edge types ('out'|'in'|'both'), up to max_depth hops."""
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

Query-layer function names (`get_node`, `get_neighbors`, `get_timeline`,
`get_causal_edges`, `find_related`) are deliberately distinct from the MCP
tool names (`get_graph_node`, `get_graph_neighbors`, `get_event_timeline`,
`get_causal_chain`, `find_related_entities`) to avoid import-name collisions
in `mcp_server.py` — same pattern as `world_state.get_state` vs the
`get_world_state` tool.

### 5. `backend/app/main.py` (modify)

```python
from app.routers import agents, graph, world, ws
# ...
app.include_router(world.router)
app.include_router(agents.router)
app.include_router(graph.router)
app.include_router(ws.router)
# ... /, /healthz, /metrics routes ...
app.mount("/", mcp.streamable_http_app(), name="mcp")  # unchanged, still last
```

## Error handling

| Condition | REST | MCP |
|---|---|---|
| `node_id` / `entity` / `node` not in `graph_nodes` | 404 | `ValueError("node not found: <id>")` |
| `from`/`to` node not found (`/path`) | 404 | n/a (no MCP `path` tool) |
| invalid `direction` | 422 (`Literal` type) | `ValueError("invalid direction: ...")` |
| `max_depth` / `limit` out of range | 422 (`Query(ge=, le=)`) | `ValueError("... must be between ...")` |
| `from_id == to_id` (`/path`) | 200, `found=true`, `path=[from_id]`, `edges=[]`, `depth=0` | n/a |
| no path found within `max_depth` | 200, `found=false`, `path=[]`, `edges=[]`, `depth=max_depth` | n/a |
| invalid/unknown `api_key` | n/a (REST uses agent header auth via `rate_limited`) | `ValueError("invalid API key")` |
| rate limit exceeded | 429 | `ValueError("rate limit exceeded (N/60s)")` |

REST `direction`/`max_depth`/`limit` validation happens via FastAPI
`Literal`/`Query` constraints (422 automatically, before the handler runs).
MCP tools re-implement the same range checks explicitly (no FastAPI layer)
— constants (`MAX_PATH_DEPTH`, `MAX_CAUSAL_DEPTH`, `MAX_RELATED_DEPTH`) live
in `graph_queries.py` so REST and MCP caps can't drift.

## Testing

`backend/tests/test_graph_queries.py` — `unittest.mock.AsyncMock` on
`pool`/`pool.fetch`/`pool.fetchrow` (no real Postgres, same pattern as
`test_mcp_server.py` / `test_graph_projection.py`):

- `get_node`: found (with edges_out/edges_in) / not found
- `get_neighbors`: `direction="out"|"in"|"both"`, `edge_type` filter, `limit` truncation, not found
- `get_path`: same node (`depth=0`), 1-hop, multi-hop, no path within `max_depth` (`found=false`), `from`/`to` not found
- `get_timeline`: with `entity` (AFFECTED edges ordered by `source_event_id`), without `entity` (global event chain ordered by numeric id), `limit`/`offset`, entity not found
- `get_causal_edges`: upstream chain (multi-depth), downstream chain, no `CAUSED` edges (empty chain), cycle doesn't loop forever (visited set), not found
- `find_related`: `edge_types` filter, `direction`, `max_depth` cutoff, `limit` cutoff, not found

`backend/tests/test_graph_router.py` (new) — FastAPI `TestClient`, mocked
`get_pool()`:
- each of the 5 endpoints: happy path (200, shape matches response_model)
- 404 cases from the table above
- 422 cases: invalid `direction`, `max_depth`/`limit` out of range

`backend/tests/test_mcp_server.py` (extend) — same pattern as the 5
existing tools:
- each of the 5 new tools: happy path, invalid `api_key` -> `ValueError`,
  rate-limit exceeded -> `ValueError`, not-found -> `ValueError`,
  out-of-range `direction`/`max_depth`/`limit` -> `ValueError`

End-to-end (manual, after deploy):
1. `GET /api/v1/graph/node/agent.<some-agent-id>` -> 200, `edges_out`
   includes `PROPOSED` edges to `event.*` nodes.
2. `GET /api/v1/graph/causal-chain?node=incident.<id>&direction=upstream`
   on an incident known to have an R1/R2 `CAUSED` edge -> chain non-empty,
   `metadata.rule_id` present.
3. MCP: `find_related_entities` on a `service.*` node -> includes its
   `team.*` owner (via `OWNED_BY`) and any `incident.*`/`alert.*`
   referencing it (via `REFERENCES`).

## Out of scope

- Any write path — `graph_nodes`/`graph_edges` remain exclusive to
  `projections/graph_projection.project_event()`.
- `find_related_entities` as a REST endpoint (MCP-only, see Architecture).
- `path` as an MCP tool (REST-only, see Architecture).
- New `CAUSED` rules (R4+) — orthogonal to the query layer.
- Grafana dashboards (Sub-project C, separate spec).
- Caching / materialized "related entities" precomputation — `graph_edges`
  is small enough today for live BFS; revisit if `find_related`/`path`
  latency becomes an issue.

## Success criteria

- All 5 REST endpoints and 5 MCP tools implemented per the signatures above
- Existing `/api/v1/world/*`, `/api/v1/agents/*`, `/mcp` (5 original tools),
  and the graph projection itself are unchanged
- `MAX_PATH_DEPTH` / `MAX_CAUSAL_DEPTH` / `MAX_RELATED_DEPTH` enforced
  identically in REST (422) and MCP (`ValueError`)
- `test_graph_queries.py`, `test_graph_router.py`,
  `test_mcp_server.py` (extended) all pass with mocked `pool`/`redis`
- Manual E2E checks above pass against the deployed `/mcp` and
  `/api/v1/graph/*`
