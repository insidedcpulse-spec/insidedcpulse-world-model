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
