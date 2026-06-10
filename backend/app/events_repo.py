import json
import uuid
from datetime import datetime

import asyncpg

from app.schemas import MemoryEntry, MemoryResponse


async def insert_pending_event(
    pool: asyncpg.Pool,
    event_id: uuid.UUID,
    agent_id: str,
    event_type: str,
    payload: dict,
) -> int:
    row = await pool.fetchrow(
        """
        INSERT INTO events (event_id, agent_id, event_type, payload, status, source)
        VALUES ($1, $2, $3, $4::jsonb, 'pending', 'queue')
        RETURNING id
        """,
        event_id, agent_id, event_type, json.dumps(payload),
    )
    return row["id"]


async def mark_processed(
    pool_or_conn,
    event_id: uuid.UUID,
    status: str,
    score: float,
    reason: str,
) -> int:
    row = await pool_or_conn.fetchrow(
        """
        UPDATE events
        SET status = $2, score = $3, reason = $4, processed_at = now()
        WHERE event_id = $1
        RETURNING id
        """,
        event_id, status, score, reason,
    )
    return row["id"]


async def insert_committed_event(
    conn: asyncpg.Connection,
    event_id: uuid.UUID,
    agent_id: str,
    event_type: str,
    payload: dict,
    score: float,
    reason: str,
    source: str,
) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO events (event_id, agent_id, event_type, payload, status, score, reason, source, processed_at)
        VALUES ($1, $2, $3, $4::jsonb, 'accepted', $5, $6, $7, now())
        RETURNING id
        """,
        event_id, agent_id, event_type, json.dumps(payload), score, reason, source,
    )
    return row["id"]


async def get_memory(
    pool: asyncpg.Pool, limit: int, offset: int, agent_id: str | None, status: str | None
) -> MemoryResponse:
    conditions = []
    params: list = []
    if agent_id:
        params.append(agent_id)
        conditions.append(f"agent_id = ${len(params)}")
    if status:
        params.append(status)
        conditions.append(f"status = ${len(params)}")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total_row = await pool.fetchrow(f"SELECT count(*) AS c FROM events {where}", *params)
    total = total_row["c"]

    params_q = params + [limit, offset]
    rows = await pool.fetch(
        f"""
        SELECT id, event_id, agent_id, event_type, status, score, reason, created_at, processed_at, payload
        FROM events {where}
        ORDER BY id DESC
        LIMIT ${len(params_q) - 1} OFFSET ${len(params_q)}
        """,
        *params_q,
    )

    items = [
        MemoryEntry(
            id=r["id"],
            event_id=r["event_id"],
            agent_id=r["agent_id"],
            event_type=r["event_type"],
            status=r["status"],
            score=float(r["score"]) if r["score"] is not None else None,
            reason=r["reason"],
            created_at=r["created_at"],
            processed_at=r["processed_at"],
            payload=json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"],
        )
        for r in rows
    ]
    return MemoryResponse(items=items, total=total, limit=limit, offset=offset)
