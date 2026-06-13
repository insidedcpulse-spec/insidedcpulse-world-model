#!/usr/bin/env python3
"""Rebuild the Graph Memory Projection from scratch by replaying accepted events.

TRUNCATEs graph_nodes/graph_edges and re-derives them by calling the exact
same project_event() used live by backend/app/worker.py — guaranteeing the
rebuilt graph matches the live one (modulo created_at/updated_at timestamps).

Run inside the api container (needs the `app` package + DB access):

    docker compose exec api python scripts/rebuild_graph_projection.py
"""

import asyncio
import json
import sys

sys.path.insert(0, "/app")  # backend/ root inside the api container

import asyncpg  # noqa: E402

from app.config import settings  # noqa: E402
from app.projections.graph_projection import project_event  # noqa: E402
from app.schemas import VisionRequest  # noqa: E402
from app.world_state import apply_op_to_value  # noqa: E402

TRUNCATE_SQL = "TRUNCATE graph_nodes, graph_edges RESTART IDENTITY"

SELECT_ACCEPTED_SQL = """
    SELECT id, agent_id, payload FROM events WHERE status = 'accepted' ORDER BY id
"""


async def rebuild_from_events(conn, events: list[dict]) -> None:
    """events: [{"id": int, "agent_id": str, "payload": VisionRequest}, ...] in id order."""
    world_state: dict[str, object] = {}

    for event in events:
        payload = event["payload"]
        applied: dict[str, dict] = {}
        for op in payload.ops:
            before = world_state.get(op.key)
            if op.op == "delete":
                after = None
                world_state.pop(op.key, None)
            else:
                after = apply_op_to_value(before, op)
                world_state[op.key] = after
            applied[op.key] = {"before": before, "after": after}

        await project_event(conn, event["id"], event["agent_id"], payload, applied)


async def main() -> None:
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=1)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(TRUNCATE_SQL)
                rows = await conn.fetch(SELECT_ACCEPTED_SQL)
                events = []
                for row in rows:
                    payload_raw = row["payload"]
                    payload_dict = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                    events.append({
                        "id": row["id"],
                        "agent_id": row["agent_id"],
                        "payload": VisionRequest(**payload_dict),
                    })
                await rebuild_from_events(conn, events)
        print(f"Rebuilt graph projection from {len(events)} accepted events.")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
