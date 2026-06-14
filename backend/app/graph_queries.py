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
