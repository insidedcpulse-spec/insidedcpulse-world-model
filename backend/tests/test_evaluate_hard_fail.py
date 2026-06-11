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


@pytest.mark.asyncio
async def test_vision_with_one_invalid_op_among_four_valid_is_hard_rejected():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    payload = VisionRequest(
        event_type="vision",
        description="Mostly valid ops with one invalid op",
        ops=[
            WorldOp(op="set", key="region.eu.status", value="stable"),
            WorldOp(op="set", key="region.eu.population", value=100),
            WorldOp(op="set", key="service.api.status", value="healthy"),
            WorldOp(op="set", key="service.api.load", value=50),
            WorldOp(op="set", key="demo.counter", value=1),
        ],
        metadata={},
    )

    score, would_accept, reasons = await evaluate(pool, AGENT, payload)

    # consistency_ratio = 4/5 = 0.8
    # score = 0.3*1 + 0.4*0.8 + 0.3*0.8 = 0.86 >= accept_score_threshold (0.5)
    assert score >= 0.5
    assert would_accept is False
    assert any("unknown key namespace 'demo.counter'" in r for r in reasons)


@pytest.mark.asyncio
async def test_vision_with_all_ops_inconsistent_is_hard_rejected():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    payload = VisionRequest(
        event_type="vision",
        description="All ops target unknown namespaces",
        ops=[
            WorldOp(op="set", key="demo.counter", value=1),
            WorldOp(op="set", key="demo.other", value=2),
        ],
        metadata={},
    )

    score, would_accept, reasons = await evaluate(pool, AGENT, payload)

    assert would_accept is False
    assert any("unknown key namespace 'demo.counter'" in r for r in reasons)
    assert any("unknown key namespace 'demo.other'" in r for r in reasons)
