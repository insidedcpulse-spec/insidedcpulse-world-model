import pytest
from unittest.mock import AsyncMock

from app.security import resolve_agent

AGENT_ROW = {
    "id": "agent-1",
    "name": "Agent One",
    "reputation": 0.5,
    "total_submitted": 10,
    "total_accepted": 8,
    "total_rejected": 2,
}


@pytest.mark.asyncio
async def test_resolve_agent_found():
    pool = AsyncMock()
    pool.fetchrow.return_value = dict(AGENT_ROW)

    agent = await resolve_agent(pool, "some-api-key")

    assert agent == AGENT_ROW
    pool.fetchrow.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_agent_not_found():
    pool = AsyncMock()
    pool.fetchrow.return_value = None

    agent = await resolve_agent(pool, "bad-key")

    assert agent is None
