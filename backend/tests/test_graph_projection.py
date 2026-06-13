import json
from unittest.mock import AsyncMock, call

import pytest

from app.projections.graph_projection import (
    UPSERT_NODE_SQL,
    UPSERT_EDGE_SQL,
    ENSURE_NODE_SQL,
    PREV_EVENT_SQL,
    EntityRef,
    parse_entity_ref,
    project_event,
    _ensure_node,
    _upsert_edge,
    _upsert_node,
)
from app.schemas import VisionRequest, WorldOp


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
