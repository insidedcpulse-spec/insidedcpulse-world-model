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
