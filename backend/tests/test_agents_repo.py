import pytest
from unittest.mock import AsyncMock

from app.agents_repo import create_agent


@pytest.mark.asyncio
async def test_create_agent_admin_defaults():
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "id": "agent-y-ab12cd",
        "name": "agent-y",
        "reputation": 0.5,
        "created_via": "admin",
    }

    agent = await create_agent(pool, "agent-y-ab12cd", "agent-y", "hash456")

    assert agent["reputation"] == 0.5
    assert agent["created_via"] == "admin"

    args = pool.fetchrow.call_args[0]
    assert args[1:] == ("agent-y-ab12cd", "agent-y", "hash456", 0.5, "admin")


@pytest.mark.asyncio
async def test_create_agent_self_serve():
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "id": "agent-x-ab12cd",
        "name": "agent-x",
        "reputation": 0.3,
        "created_via": "self_serve",
    }

    agent = await create_agent(
        pool, "agent-x-ab12cd", "agent-x", "hash123",
        reputation=0.3, created_via="self_serve",
    )

    assert agent["reputation"] == 0.3
    assert agent["created_via"] == "self_serve"

    args = pool.fetchrow.call_args[0]
    assert args[1:] == ("agent-x-ab12cd", "agent-x", "hash123", 0.3, "self_serve")
