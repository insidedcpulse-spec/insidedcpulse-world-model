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
