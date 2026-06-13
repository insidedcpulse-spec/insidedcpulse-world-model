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
    REFERENCES_TO_SQL,
    UPSERT_EDGE_SQL,
    UPSERT_NODE_SQL,
    _ensure_node,
    _rule_r1_explicit_ref,
    _rule_r2_alert_precedes_incident,
    _rule_r3_deployment_precedes_degradation,
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


@pytest.mark.asyncio
async def test_r3_matches_deployment_in_progress_before_degradation():
    conn = AsyncMock()
    conn.fetch.side_effect = [
        [{"source_node": "deployment.checkout_v2"}],  # deployments referencing service.checkout
        [{"target_node": "deployment.checkout_v2", "source_event_id": 295, "metadata": {"fields": {"status": "in_progress"}}}],
    ]
    applied = {"service.checkout.status": {"before": "healthy", "after": "degraded"}}

    edges = await _rule_r3_deployment_precedes_degradation(conn, 300, _vision(), applied)

    assert edges == [
        CausalEdge("deployment.checkout_v2", "service.checkout", 0.7 * 0.9, {
            "rule_id": "deployment_precedes_degradation", "evidence_event_id": 295,
        })
    ]
    conn.fetch.assert_any_await(REFERENCES_TO_SQL, "service.checkout", "deployment.%")
    conn.fetch.assert_any_await(RECENT_AFFECTED_SQL, "deployment.%", 300, CAUSAL_WINDOW)


@pytest.mark.asyncio
async def test_r3_skips_when_already_degraded():
    conn = AsyncMock()
    applied = {"service.checkout.status": {"before": "degraded", "after": "degraded"}}

    edges = await _rule_r3_deployment_precedes_degradation(conn, 300, _vision(), applied)

    assert edges == []
    conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_r3_no_match_when_no_referencing_deployment():
    conn = AsyncMock()
    conn.fetch.side_effect = [[]]
    applied = {"service.checkout.status": {"before": "healthy", "after": "degraded"}}

    edges = await _rule_r3_deployment_precedes_degradation(conn, 300, _vision(), applied)

    assert edges == []


@pytest.mark.asyncio
async def test_r3_no_match_when_deployment_status_not_in_progress_or_done():
    conn = AsyncMock()
    conn.fetch.side_effect = [
        [{"source_node": "deployment.checkout_v2"}],
        [{"target_node": "deployment.checkout_v2", "source_event_id": 295, "metadata": {"fields": {"status": "pending"}}}],
    ]
    applied = {"service.checkout.status": {"before": "healthy", "after": "degraded"}}

    edges = await _rule_r3_deployment_precedes_degradation(conn, 300, _vision(), applied)

    assert edges == []


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from app.world_state import apply_op_to_value  # noqa: E402


class FakeConn:
    """Minimal in-memory stand-in for asyncpg.Connection, covering exactly the
    SQL shapes graph_projection.py issues. Used only for the replay-determinism
    test below."""

    def __init__(self, events):
        self.events = events  # [{"id": int, "status": "accepted"}]
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str, str], dict] = {}

    @staticmethod
    def _norm(sql):
        return " ".join(sql.split())

    async def execute(self, sql, *args):
        s = self._norm(sql)
        if s.startswith("TRUNCATE"):
            self.nodes.clear()
            self.edges.clear()
        elif "INSERT INTO graph_nodes (id, type, label, metadata" in s:
            node_id, node_type, label, metadata_json = args
            self.nodes[node_id] = {"type": node_type, "label": label, "metadata": json.loads(metadata_json)}
        elif "INSERT INTO graph_nodes (id, type, label)" in s:
            node_id, node_type, label = args
            self.nodes.setdefault(node_id, {"type": node_type, "label": label, "metadata": {}})
        elif "INSERT INTO graph_edges" in s:
            source, target, edge_type, weight, metadata_json, source_event_id = args
            self.edges[(source, target, edge_type)] = {
                "weight": weight, "metadata": json.loads(metadata_json), "source_event_id": source_event_id,
            }
        else:
            raise AssertionError(f"unexpected SQL in FakeConn.execute: {s!r}")

    async def fetchrow(self, sql, *args):
        s = self._norm(sql)
        if "FROM events" in s:
            (before_id,) = args
            candidates = [e for e in self.events if e["status"] == "accepted" and e["id"] < before_id]
            if not candidates:
                return None
            return {"id": max(e["id"] for e in candidates)}
        raise AssertionError(f"unexpected SQL in FakeConn.fetchrow: {s!r}")

    async def fetch(self, sql, *args):
        s = self._norm(sql)
        if "SELECT target_node FROM graph_edges WHERE source_node" in s:
            (source,) = args
            return [
                {"target_node": t} for (src, t, et) in self.edges if src == source and et == "REFERENCES"
            ]
        if "SELECT source_node FROM graph_edges WHERE target_node" in s:
            target, prefix = args
            pfx = prefix.rstrip("%")
            return [
                {"source_node": src} for (src, tgt, et) in self.edges
                if tgt == target and et == "REFERENCES" and src.startswith(pfx)
            ]
        if "WHERE edge_type = 'AFFECTED'" in s:
            prefix, before_id, window = args
            pfx = prefix.rstrip("%")
            out = []
            for (src, tgt, et), data in self.edges.items():
                if et == "AFFECTED" and tgt.startswith(pfx):
                    sev = data["source_event_id"]
                    if sev < before_id and sev >= before_id - window:
                        out.append({"target_node": tgt, "source_event_id": sev, "metadata": data["metadata"]})
            return out
        raise AssertionError(f"unexpected SQL in FakeConn.fetch: {s!r}")


def _fixture_events():
    return [
        (1, "deploy-agent-aaaaaa", VisionRequest(
            event_type="vision",
            description="Start checkout v2 deployment",
            ops=[
                WorldOp(op="set", key="deployment.checkout_v2.status", value="in_progress"),
                WorldOp(op="set", key="deployment.checkout_v2.target_service", value="service.checkout"),
            ],
        )),
        (2, "alert-agent-bbbbbb", VisionRequest(
            event_type="vision",
            description="Checkout latency alert firing",
            ops=[
                WorldOp(op="set", key="alert.a1.status", value="firing"),
                WorldOp(op="set", key="alert.a1.source_service", value="service.checkout"),
            ],
        )),
        (3, "sre-agent-cccccc", VisionRequest(
            event_type="vision",
            description="Open incident for checkout",
            ops=[
                WorldOp(op="set", key="incident.inc3.status", value="open"),
                WorldOp(op="set", key="incident.inc3.affected_service", value="service.checkout"),
            ],
        )),
    ]


@pytest.mark.asyncio
async def test_rebuild_reproduces_live_projection():
    from rebuild_graph_projection import rebuild_from_events

    fixture = _fixture_events()
    events_meta = [{"id": event_id, "status": "accepted"} for event_id, _, _ in fixture]

    live = FakeConn(events_meta)
    world_state: dict[str, object] = {}
    for event_id, agent_id, payload in fixture:
        applied = {}
        for op in payload.ops:
            before = world_state.get(op.key)
            after = None if op.op == "delete" else apply_op_to_value(before, op)
            applied[op.key] = {"before": before, "after": after}
            if op.op == "delete":
                world_state.pop(op.key, None)
            else:
                world_state[op.key] = after
        await project_event(live, event_id, agent_id, payload, applied)

    rebuilt = FakeConn(events_meta)
    await rebuild_from_events(rebuilt, [
        {"id": event_id, "agent_id": agent_id, "payload": payload} for event_id, agent_id, payload in fixture
    ])

    assert rebuilt.nodes == live.nodes
    assert rebuilt.edges == live.edges
    # Sanity: R2 should have fired (alert.a1 -CAUSED-> incident.inc3)
    assert ("alert.a1", "incident.inc3", "CAUSED") in live.edges
