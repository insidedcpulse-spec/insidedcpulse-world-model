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
