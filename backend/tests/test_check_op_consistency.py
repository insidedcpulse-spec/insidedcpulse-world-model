import json
from unittest.mock import AsyncMock

import pytest

from app.schemas import WorldOp
from app.validation import check_op_consistency


@pytest.mark.asyncio
async def test_accepts_valid_domain_set_with_no_current_value():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    op = WorldOp(op="set", key="service.api.status", value="healthy")
    ok, msg = await check_op_consistency(pool, op)

    assert (ok, msg) == (True, None)


@pytest.mark.asyncio
async def test_rejects_unknown_namespace():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    op = WorldOp(op="set", key="world.status", value="building")
    ok, msg = await check_op_consistency(pool, op)

    assert ok is False
    assert msg == "unknown key namespace 'world.status'"


@pytest.mark.asyncio
async def test_rejects_increment_overflow_against_current_value():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"value": json.dumps(95)})

    op = WorldOp(op="increment", key="service.api.load", value=10)
    ok, msg = await check_op_consistency(pool, op)

    assert ok is False
    assert "above maximum 100" in msg


@pytest.mark.asyncio
async def test_generic_type_check_runs_before_domain_check():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"value": json.dumps("healthy")})

    op = WorldOp(op="increment", key="service.api.status", value=1)
    ok, msg = await check_op_consistency(pool, op)

    assert ok is False
    assert "is not numeric, cannot increment" in msg


@pytest.mark.asyncio
async def test_delete_always_allowed_regardless_of_namespace():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    op = WorldOp(op="delete", key="not.a.domain.key")
    ok, msg = await check_op_consistency(pool, op)

    assert (ok, msg) == (True, None)
