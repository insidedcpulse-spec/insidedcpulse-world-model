"""Deterministic validation layer.

LLM-submitted visions are NEVER trusted. Every check here is a fixed,
reproducible rule — no model calls, no heuristics that depend on external
services. Same input -> same verdict, always.
"""

import hashlib
import json

import asyncpg
import redis.asyncio as redis

from app.config import settings
from app.schemas import VisionRequest, WorldOp
from app.world_schema import ENTITY_SCHEMAS, parse_key


def ops_hash(ops: list[WorldOp]) -> str:
    canonical = json.dumps(
        [op.model_dump() for op in ops], sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def payload_hash(agent_id: str, payload: VisionRequest) -> str:
    canonical = json.dumps(
        {"agent_id": agent_id, "description": payload.description,
         "ops": [op.model_dump() for op in payload.ops]},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def estimate_size(payload: VisionRequest) -> int:
    return len(payload.model_dump_json().encode("utf-8"))


def _is_numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_field_value(field: str, spec: dict, value) -> str | None:
    """Return an error message if value violates the field spec, else None."""
    field_type = spec["type"]

    if field_type in ("number", "integer"):
        if not _is_numeric(value):
            return f"'{field}' must be a number, got {value!r}"
        if field_type == "integer" and float(value) != int(value):
            return f"'{field}' must be an integer, got {value!r}"
        if "min" in spec and value < spec["min"]:
            return f"value {value} for '{field}' below minimum {spec['min']}"
        if "max" in spec and value > spec["max"]:
            return f"value {value} for '{field}' above maximum {spec['max']}"
        return None

    if field_type == "string":
        if not isinstance(value, str):
            return f"'{field}' must be a string, got {value!r}"
        return None

    if field_type == "enum":
        if value not in spec["values"]:
            return f"'{field}' must be one of {spec['values']}, got {value!r}"
        return None

    if field_type == "object":
        if not isinstance(value, dict):
            return f"'{field}' must be an object, got {value!r}"
        return None

    return None


def check_domain_consistency(op: WorldOp, current) -> tuple[bool, str | None]:
    """Check an op against the domain key schema (app.world_schema).

    `current` is the already-deserialized current value of op.key (or
    None). `delete` is always allowed regardless of namespace.
    """
    if op.op == "delete":
        return True, None

    parts = parse_key(op.key)
    if parts is None:
        return False, f"unknown key namespace '{op.key}'"

    spec = ENTITY_SCHEMAS[parts.entity].get(parts.field)
    if spec is None:
        return False, f"unknown field '{parts.field}' for entity '{parts.entity}'"

    field_type = spec["type"]

    if op.op == "merge" and field_type != "object":
        return False, f"op 'merge' not allowed on field '{parts.field}' (type '{field_type}')"
    if op.op == "increment" and field_type not in ("number", "integer"):
        return False, f"op 'increment' not allowed on field '{parts.field}' (type '{field_type}')"

    if op.op == "increment":
        result = (current or 0) + op.value
    elif op.op == "merge":
        base = current if isinstance(current, dict) else {}
        result = {**base, **op.value}
    else:
        result = op.value

    error = _validate_field_value(parts.field, spec, result)
    if error:
        return False, error

    return True, None


async def check_op_consistency(pool: asyncpg.Pool, op: WorldOp) -> tuple[bool, str | None]:
    """Check a single op against the current world_state for type consistency."""
    row = await pool.fetchrow("SELECT value FROM world_state WHERE key = $1", op.key)
    current = row["value"] if row else None
    if current is not None:
        import json as _json
        current = _json.loads(current) if isinstance(current, str) else current

    if op.op == "delete":
        return True, None

    if op.op == "increment":
        if not _is_numeric(op.value):
            return False, f"increment on '{op.key}' requires a numeric value"
        if current is not None and not _is_numeric(current):
            return False, f"key '{op.key}' is not numeric, cannot increment"
        return True, None

    if op.op == "merge":
        if not isinstance(op.value, dict):
            return False, f"merge on '{op.key}' requires an object value"
        if current is not None and not isinstance(current, dict):
            return False, f"key '{op.key}' is not an object, cannot merge"
        return True, None

    if op.op == "set":
        if op.value is None:
            return False, f"set on '{op.key}' requires a non-null value"
        return True, None

    return False, f"unknown op '{op.op}'"


async def check_duplicate(r: redis.Redis, agent_id: str, payload: VisionRequest) -> bool:
    """Anti-spam: identical (agent, description, ops) submitted again within 60s."""
    key = f"dedup:{agent_id}:{payload_hash(agent_id, payload)}"
    was_set = await r.set(key, "1", nx=True, ex=60)
    return not was_set  # True => duplicate


async def evaluate(
    pool: asyncpg.Pool,
    agent: dict,
    payload: VisionRequest,
) -> tuple[float, bool, list[str]]:
    """Run all deterministic checks and return (score, would_accept, reasons).

    Dedup/spam is handled separately at the API edge (check_duplicate),
    not here — this function must be safe to re-run by the worker without
    flagging the submission it is currently re-validating as a duplicate
    of itself.
    """
    reasons: list[str] = []
    hard_fail = False

    # 1. Size limit
    size = estimate_size(payload)
    if size > settings.max_payload_bytes:
        reasons.append(f"payload too large ({size} > {settings.max_payload_bytes} bytes)")
        hard_fail = True

    # 2. Reputation gate
    if agent["reputation"] < settings.min_reputation_to_submit:
        reasons.append(f"agent reputation too low ({agent['reputation']:.4f})")
        hard_fail = True

    # 3. Per-op consistency against current world state
    consistent = 0
    for op in payload.ops:
        ok, msg = await check_op_consistency(pool, op)
        if ok:
            consistent += 1
        else:
            reasons.append(msg or f"op on '{op.key}' inconsistent")
    consistency_ratio = consistent / len(payload.ops) if payload.ops else 0.0
    if consistency_ratio < 1.0:
        hard_fail = hard_fail or consistency_ratio == 0.0

    # 4. Completeness (already enforced by pydantic, but score it anyway)
    completeness = 1.0 if payload.description.strip() and payload.ops else 0.0

    score = round(
        0.3 * completeness + 0.4 * consistency_ratio + 0.3 * float(agent["reputation"]),
        4,
    )

    would_accept = (not hard_fail) and score >= settings.accept_score_threshold
    if not reasons:
        reasons.append("all checks passed")

    return score, would_accept, reasons
