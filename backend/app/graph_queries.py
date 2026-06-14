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
