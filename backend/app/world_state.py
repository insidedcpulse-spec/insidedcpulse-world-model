"""World state: a materialized projection, derived only by replaying accepted events.

Nothing here is ever written directly by an external request — only the
worker (after validation passes) calls commit_ops().
"""

import json
from datetime import datetime, timezone

import asyncpg

from app.schemas import SimulateOpResult, WorldOp, WorldStateEntry, WorldStateResponse
from app.validation import check_op_consistency


def _load(value):
    if value is None:
        return None
    return json.loads(value) if isinstance(value, str) else value


def apply_op_to_value(current, op: WorldOp):
    if op.op == "set":
        return op.value
    if op.op == "merge":
        base = current if isinstance(current, dict) else {}
        return {**base, **op.value}
    if op.op == "increment":
        return (current or 0) + op.value
    if op.op == "delete":
        return None
    raise ValueError(f"unknown op {op.op}")


async def get_state(pool: asyncpg.Pool) -> WorldStateResponse:
    rows = await pool.fetch("SELECT key, value, version, updated_at FROM world_state ORDER BY key")
    state = {
        row["key"]: WorldStateEntry(
            key=row["key"],
            value=_load(row["value"]),
            version=row["version"],
            updated_at=row["updated_at"],
        )
        for row in rows
    }
    return WorldStateResponse(state=state, as_of=datetime.now(timezone.utc))


async def simulate_ops(
    pool: asyncpg.Pool, ops: list[WorldOp]
) -> tuple[list[SimulateOpResult], bool, list[str]]:
    results: list[SimulateOpResult] = []
    reasons: list[str] = []
    valid = True

    for op in ops:
        ok, msg = await check_op_consistency(pool, op)
        if not ok:
            valid = False
            reasons.append(msg or f"invalid op on '{op.key}'")
            results.append(SimulateOpResult(key=op.key, op=op.op, before=None, after=None))
            continue

        row = await pool.fetchrow("SELECT value FROM world_state WHERE key = $1", op.key)
        before = _load(row["value"]) if row else None
        after = apply_op_to_value(before, op)
        results.append(SimulateOpResult(key=op.key, op=op.op, before=before, after=after))

    if not reasons:
        reasons.append("simulation valid against current world state")

    return results, valid, reasons


async def commit_ops(
    conn: asyncpg.Connection, ops: list[WorldOp], event_id: int
) -> dict[str, dict]:
    """Apply ops to world_state inside an existing transaction. Returns {key: {before, after}}."""
    applied: dict[str, dict] = {}

    for op in ops:
        row = await conn.fetchrow("SELECT value, version FROM world_state WHERE key = $1 FOR UPDATE", op.key)
        before = _load(row["value"]) if row else None
        version = row["version"] if row else 0

        if op.op == "delete":
            await conn.execute("DELETE FROM world_state WHERE key = $1", op.key)
            applied[op.key] = {"before": before, "after": None}
            continue

        after = apply_op_to_value(before, op)
        await conn.execute(
            """
            INSERT INTO world_state (key, value, version, updated_by_event, updated_at)
            VALUES ($1, $2::jsonb, 1, $3, now())
            ON CONFLICT (key) DO UPDATE
            SET value = $2::jsonb, version = world_state.version + 1,
                updated_by_event = $3, updated_at = now()
            """,
            op.key, json.dumps(after), event_id,
        )
        applied[op.key] = {"before": before, "after": after}

    return applied
