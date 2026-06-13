"""Graph Memory Projection: a second, derived projection over accepted events.

Populated only by project_event(), called from worker.py (live) and
scripts/rebuild_graph_projection.py (replay). Never written by agents or
API handlers directly. See
docs/superpowers/specs/2026-06-13-graph-memory-projection-design.md.
"""

import json
import re
from typing import NamedTuple

from app.world_schema import ENTITY_SCHEMAS, parse_key

ENTITY_REF_PATTERN = re.compile(r"^([a-z][a-z0-9_]*)\.([a-z0-9_]{1,32})$")

REFERENCE_FIELDS = {"affected_service", "affected_region", "source_service", "target_service"}
R1_SUFFIXES = ("_deployment_id", "_incident_id", "_alert_id")
CAUSAL_WINDOW = 50


class EntityRef(NamedTuple):
    entity: str
    entity_id: str


class CausalEdge(NamedTuple):
    source: str
    target: str
    confidence: float
    metadata: dict


def parse_entity_ref(value) -> EntityRef | None:
    """Parse a 2-segment '<entity>.<entity_id>' reference value (not a world_state key)."""
    if not isinstance(value, str):
        return None
    match = ENTITY_REF_PATTERN.match(value)
    if not match:
        return None
    entity, entity_id = match.groups()
    if entity not in ENTITY_SCHEMAS:
        return None
    return EntityRef(entity, entity_id)


def _node_type_and_label(node_id: str) -> tuple[str, str]:
    prefix, _, rest = node_id.partition(".")
    if prefix == "agent":
        return "agent", rest
    return prefix, node_id


UPSERT_NODE_SQL = """
    INSERT INTO graph_nodes (id, type, label, metadata, updated_at)
    VALUES ($1, $2, $3, $4::jsonb, now())
    ON CONFLICT (id) DO UPDATE
    SET label = $3, metadata = $4::jsonb, updated_at = now()
"""

ENSURE_NODE_SQL = """
    INSERT INTO graph_nodes (id, type, label)
    VALUES ($1, $2, $3)
    ON CONFLICT (id) DO NOTHING
"""

UPSERT_EDGE_SQL = """
    INSERT INTO graph_edges (source_node, target_node, edge_type, weight, metadata, source_event_id)
    VALUES ($1, $2, $3, $4, $5::jsonb, $6)
    ON CONFLICT (source_node, target_node, edge_type) DO UPDATE
    SET weight = $4, metadata = $5::jsonb, source_event_id = $6
"""


async def _upsert_node(conn, node_id, node_type=None, label=None, metadata=None):
    if node_type is None or label is None:
        derived_type, derived_label = _node_type_and_label(node_id)
        node_type = node_type or derived_type
        label = label or derived_label
    await conn.execute(UPSERT_NODE_SQL, node_id, node_type, label, json.dumps(metadata or {}))


async def _ensure_node(conn, node_id):
    node_type, label = _node_type_and_label(node_id)
    await conn.execute(ENSURE_NODE_SQL, node_id, node_type, label)


async def _upsert_edge(conn, source, target, edge_type, weight=1.0, metadata=None, source_event_id=None):
    await conn.execute(
        UPSERT_EDGE_SQL, source, target, edge_type, weight, json.dumps(metadata or {}), source_event_id
    )


PREV_EVENT_SQL = """
    SELECT id FROM events WHERE status = 'accepted' AND id < $1 ORDER BY id DESC LIMIT 1
"""


async def project_event(conn, event_db_id, agent_id, payload, applied: dict[str, dict]) -> None:
    agent_node_id = f"agent.{agent_id}"
    await _upsert_node(conn, agent_node_id)

    event_node_id = f"event.{event_db_id}"
    await _upsert_node(
        conn, event_node_id, "event", payload.description, {"event_type": payload.event_type}
    )
    await _upsert_edge(conn, agent_node_id, event_node_id, "PROPOSED", source_event_id=event_db_id)

    prev = await conn.fetchrow(PREV_EVENT_SQL, event_db_id)
    if prev is not None:
        await _upsert_edge(
            conn, f"event.{prev['id']}", event_node_id, "PRECEDES", source_event_id=event_db_id
        )

    affected_counts: dict[str, int] = {}
    affected_fields: dict[str, dict] = {}
    for key, change in applied.items():
        parts = parse_key(key)
        if parts is None:
            continue
        entity_node_id = f"{parts.entity}.{parts.entity_id}"
        affected_counts[entity_node_id] = affected_counts.get(entity_node_id, 0) + 1
        affected_fields.setdefault(entity_node_id, {})[parts.field] = change["after"]
        await _upsert_node(conn, entity_node_id)

    for entity_node_id, count in affected_counts.items():
        await _upsert_edge(
            conn, event_node_id, entity_node_id, "AFFECTED",
            weight=count, metadata={"fields": affected_fields[entity_node_id]},
            source_event_id=event_db_id,
        )

    for key, change in applied.items():
        parts = parse_key(key)
        if parts is None:
            continue
        entity_node_id = f"{parts.entity}.{parts.entity_id}"
        after = change["after"]

        if parts.field in REFERENCE_FIELDS:
            ref = parse_entity_ref(after)
            if ref is not None:
                target_id = f"{ref.entity}.{ref.entity_id}"
                await _upsert_node(conn, target_id)
                await _upsert_edge(conn, entity_node_id, target_id, "REFERENCES", source_event_id=event_db_id)

        if parts.entity == "team" and parts.field == "owned_services" and isinstance(after, dict):
            for svc in after.get("services", []):
                svc_node_id = f"service.{svc}"
                await _upsert_node(conn, svc_node_id)
                await _upsert_edge(conn, svc_node_id, entity_node_id, "OWNED_BY", source_event_id=event_db_id)

    for edge in await _causal_edges(conn, event_db_id, payload, applied):
        await _ensure_node(conn, edge.source)
        await _ensure_node(conn, edge.target)
        await _upsert_edge(
            conn, edge.source, edge.target, "CAUSED",
            weight=edge.confidence, metadata=edge.metadata, source_event_id=event_db_id,
        )


async def _causal_edges(conn, event_db_id, payload, applied) -> list:
    edges: list = []
    for rule in CAUSAL_RULES:
        edges.extend(await rule(conn, event_db_id, payload, applied))
    return edges


CAUSAL_RULES: list = []
