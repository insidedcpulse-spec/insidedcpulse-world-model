import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas import VisionRequest, WorldOp
from app.worker import process_event


@pytest.mark.asyncio
async def test_process_event_accepted_calls_project_event_in_same_transaction():
    event_id = uuid.uuid4()
    agent_id = "sre-agent-212dbc"
    payload = VisionRequest(
        event_type="vision",
        description="Mark incident mitigated",
        ops=[WorldOp(op="set", key="incident.inc3.status", value="mitigated")],
    )
    data = {"event_id": str(event_id), "agent_id": agent_id, "payload": payload.model_dump()}

    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "id": agent_id, "reputation": 0.6, "total_submitted": 10, "total_accepted": 8, "total_rejected": 2,
    }

    conn = AsyncMock()
    conn.transaction = MagicMock()
    conn.transaction.return_value.__aenter__.return_value = None
    conn.transaction.return_value.__aexit__.return_value = None

    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    pool.acquire.return_value.__aexit__.return_value = None

    r = AsyncMock()
    r.get.return_value = None

    applied = {"incident.inc3.status": {"before": "open", "after": "mitigated"}}

    with (
        patch("app.worker.evaluate", new=AsyncMock(return_value=(0.9, True, ["looks good"]))),
        patch("app.worker.mark_processed", new=AsyncMock(return_value=99)),
        patch("app.worker.commit_ops", new=AsyncMock(return_value=applied)),
        patch("app.worker.apply_outcome", new=AsyncMock(return_value=0.62)),
        patch("app.worker.graph_projection.project_event", new=AsyncMock()) as project_event_mock,
    ):
        await process_event(pool, r, data)

    project_event_mock.assert_awaited_once_with(conn, 99, agent_id, payload, applied)
