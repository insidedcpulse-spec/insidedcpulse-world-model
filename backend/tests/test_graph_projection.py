import json
from unittest.mock import AsyncMock, call

import pytest

from app.projections.graph_projection import (
    CausalEdge,
    CAUSAL_WINDOW,
    ENSURE_NODE_SQL,
    EntityRef,
    PREV_EVENT_SQL,
    RECENT_AFFECTED_SQL,
    REFERENCES_FROM_SQL,
    UPSERT_EDGE_SQL,
    UPSERT_NODE_SQL,
    _ensure_node,
    _rule_r1_explicit_ref,
    _rule_r2_alert_precedes_incident,
    _upsert_edge,
    _upsert_node,
    parse_entity_ref,
    project_event,
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


@pytest.mark.asyncio
async def test_r1_explicit_ref_in_notes_field():
    conn = AsyncMock()
    applied = {
        "incident.inc3.notes": {
            "before": {},
            "after": {"scaling_deployment_id": "deployment.checkout_scaling"},
        },
    }

    edges = await _rule_r1_explicit_ref(conn, 100, _vision(), applied)

    assert edges == [
        CausalEdge("incident.inc3", "deployment.checkout_scaling", 1.0, {"rule_id": "explicit_ref"})
    ]


@pytest.mark.asyncio
async def test_r1_no_match_when_notes_has_no_ref_suffix_field():
    conn = AsyncMock()
    applied = {
        "incident.inc3.notes": {"before": {}, "after": {"summary": "investigating"}},
    }

    edges = await _rule_r1_explicit_ref(conn, 100, _vision(), applied)

    assert edges == []


@pytest.mark.asyncio
async def test_r1_no_match_when_ref_value_does_not_parse():
    conn = AsyncMock()
    applied = {
        "incident.inc3.notes": {
            "before": {},
            "after": {"scaling_deployment_id": "not-a-ref"},
        },
    }

    edges = await _rule_r1_explicit_ref(conn, 100, _vision(), applied)

    assert edges == []


@pytest.mark.asyncio
async def test_project_event_creates_caused_edge_for_r1():
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    conn.fetch.return_value = []

    applied = {
        "incident.inc3.notes": {
            "before": {},
            "after": {"scaling_deployment_id": "deployment.checkout_scaling"},
        },
    }
    await project_event(conn, 100, "sre-agent-212dbc", _vision(), applied)

    conn.execute.assert_any_await(
        ENSURE_NODE_SQL, "incident.inc3", "incident", "incident.inc3"
    )
    conn.execute.assert_any_await(
        ENSURE_NODE_SQL, "deployment.checkout_scaling", "deployment", "deployment.checkout_scaling"
    )
    conn.execute.assert_any_await(
        UPSERT_EDGE_SQL, "incident.inc3", "deployment.checkout_scaling", "CAUSED", 1.0,
        json.dumps({"rule_id": "explicit_ref"}), 100,
    )


@pytest.mark.asyncio
async def test_r2_matches_alert_firing_for_same_service():
    conn = AsyncMock()
    conn.fetch.side_effect = [
        [{"target_node": "service.checkout"}],  # incident.inc3's REFERENCES targets
        [{"target_node": "alert.a1", "source_event_id": 190, "metadata": {"fields": {"status": "firing"}}}],
        [{"target_node": "service.checkout"}],  # alert.a1's REFERENCES targets
    ]
    applied = {"incident.inc3.status": {"before": None, "after": "open"}}

    edges = await _rule_r2_alert_precedes_incident(conn, 200, _vision(), applied)

    assert edges == [
        CausalEdge("alert.a1", "incident.inc3", 0.7 * 0.8, {
            "rule_id": "alert_precedes_incident", "evidence_event_id": 190,
        })
    ]
    conn.fetch.assert_any_await(REFERENCES_FROM_SQL, "incident.inc3")
    conn.fetch.assert_any_await(RECENT_AFFECTED_SQL, "alert.%", 200, CAUSAL_WINDOW)
    conn.fetch.assert_any_await(REFERENCES_FROM_SQL, "alert.a1")


@pytest.mark.asyncio
async def test_r2_no_match_when_incident_has_no_references():
    conn = AsyncMock()
    conn.fetch.side_effect = [[]]  # incident.inc3 has no REFERENCES edges
    applied = {"incident.inc3.status": {"before": None, "after": "open"}}

    edges = await _rule_r2_alert_precedes_incident(conn, 200, _vision(), applied)

    assert edges == []


@pytest.mark.asyncio
async def test_r2_skips_when_status_was_already_set():
    conn = AsyncMock()
    applied = {"incident.inc3.status": {"before": "open", "after": "mitigated"}}

    edges = await _rule_r2_alert_precedes_incident(conn, 200, _vision(), applied)

    assert edges == []
    conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_r2_no_match_when_no_common_reference_target():
    conn = AsyncMock()
    conn.fetch.side_effect = [
        [{"target_node": "service.checkout"}],
        [{"target_node": "alert.a1", "source_event_id": 190, "metadata": {"fields": {"status": "firing"}}}],
        [{"target_node": "service.payments"}],  # alert.a1 references a different service
    ]
    applied = {"incident.inc3.status": {"before": None, "after": "open"}}

    edges = await _rule_r2_alert_precedes_incident(conn, 200, _vision(), applied)

    assert edges == []
