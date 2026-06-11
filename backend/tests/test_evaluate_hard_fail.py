from unittest.mock import AsyncMock

import pytest

from app.schemas import VisionRequest, WorldOp
from app.validation import evaluate

AGENT = {"id": "agent-1", "reputation": 0.8, "total_submitted": 10, "total_accepted": 8, "total_rejected": 2}


@pytest.mark.asyncio
async def test_vision_with_one_inconsistent_op_is_hard_rejected():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    payload = VisionRequest(
        event_type="vision",
        description="Mixed valid and invalid ops",
        ops=[
            WorldOp(op="set", key="region.eu.status", value="stable"),
            WorldOp(op="set", key="demo.counter", value=1),
        ],
        metadata={},
    )

    score, would_accept, reasons = await evaluate(pool, AGENT, payload)

    assert would_accept is False
    assert any("unknown key namespace 'demo.counter'" in r for r in reasons)
