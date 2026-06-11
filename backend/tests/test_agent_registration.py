import pytest
from unittest.mock import AsyncMock, patch

from app.agent_registration import SELF_SERVE_INITIAL_REPUTATION, register_self_agent
from app.rate_limit import RateLimitExceeded
from app.security import hash_api_key


@pytest.mark.asyncio
async def test_register_self_agent_success():
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "id": "my-agent-ab12cd",
        "name": "my-agent",
        "reputation": 0.3,
        "created_via": "self_serve",
    }

    with patch("app.agent_registration.enforce_ip_rate_limit", AsyncMock()) as enforce_mock:
        result = await register_self_agent(pool, "my-agent", "203.0.113.5")

    enforce_mock.assert_awaited_once_with("203.0.113.5", 5, 86400)
    assert result["agent_id"] == "my-agent-ab12cd"
    assert result["reputation"] == SELF_SERVE_INITIAL_REPUTATION == 0.3

    insert_args = pool.fetchrow.call_args[0]
    assert insert_args[3] == hash_api_key(result["api_key"])
    assert insert_args[4] == 0.3
    assert insert_args[5] == "self_serve"


@pytest.mark.asyncio
async def test_register_self_agent_slugifies_name():
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "id": "my-cool-agent-ab12cd",
        "name": "My Cool Agent!",
        "reputation": 0.3,
        "created_via": "self_serve",
    }

    with patch("app.agent_registration.enforce_ip_rate_limit", AsyncMock()):
        await register_self_agent(pool, "My Cool Agent!", "203.0.113.5")

    agent_id = pool.fetchrow.call_args[0][1]
    assert agent_id.startswith("my-cool-agent-")


@pytest.mark.asyncio
async def test_register_self_agent_rate_limited():
    pool = AsyncMock()

    with patch(
        "app.agent_registration.enforce_ip_rate_limit",
        AsyncMock(side_effect=RateLimitExceeded(5, 86400)),
    ):
        with pytest.raises(RateLimitExceeded):
            await register_self_agent(pool, "my-agent", "203.0.113.5")

    pool.fetchrow.assert_not_called()
