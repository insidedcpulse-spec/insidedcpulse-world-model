"""Domain schema for world_state keys: <entity>.<id>.<field>.

This is the first real domain for InsideDCPulse: an
infrastructure/project status world (regions and services). Only keys
matching this schema pass app.validation.check_domain_consistency.
"""

import re
from typing import NamedTuple, TypedDict

KEY_PATTERN = re.compile(r"^([a-z][a-z0-9_]*)\.([a-z0-9_]{1,32})\.([a-z][a-z0-9_]*)$")


class FieldSpec(TypedDict, total=False):
    type: str  # "number" | "integer" | "string" | "enum" | "object"
    min: float
    max: float
    values: list[str]


ENTITY_SCHEMAS: dict[str, dict[str, FieldSpec]] = {
    "region": {
        "capacity_forecast": {"type": "number", "min": 0},
        "population": {"type": "integer", "min": 0},
        "status": {"type": "enum", "values": ["stable", "growing", "declining", "critical"]},
        "notes": {"type": "object"},
    },
    "service": {
        "status": {"type": "enum", "values": ["healthy", "degraded", "down"]},
        "load": {"type": "number", "min": 0, "max": 100},
        "version": {"type": "string"},
        "capacity": {"type": "number", "min": 0},
    },
    "incident": {
        "severity": {"type": "enum", "values": ["low", "medium", "high", "critical"]},
        "status": {"type": "enum", "values": ["open", "mitigated", "resolved"]},
        "affected_service": {"type": "string"},
        "affected_region": {"type": "string"},
        "notes": {"type": "object"},
    },
}


class KeyParts(NamedTuple):
    entity: str
    entity_id: str
    field: str


def parse_key(key: str) -> KeyParts | None:
    """Parse '<entity>.<id>.<field>' if entity is a known schema, else None."""
    match = KEY_PATTERN.match(key)
    if not match:
        return None
    entity, entity_id, field = match.groups()
    if entity not in ENTITY_SCHEMAS:
        return None
    return KeyParts(entity, entity_id, field)


def get_field_spec(entity: str, field: str) -> FieldSpec | None:
    return ENTITY_SCHEMAS.get(entity, {}).get(field)
