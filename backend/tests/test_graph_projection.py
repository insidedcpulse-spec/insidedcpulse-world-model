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
