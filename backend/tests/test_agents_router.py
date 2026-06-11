import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.rate_limit import RateLimitExceeded
from app.routers.agents import register_self
from app.schemas import AgentRegisterRequest


def _make_request(forwarded_for: str = "", client_host: str = "127.0.0.1"):
    request = MagicMock()
    request.headers.get.return_value = forwarded_for
    request.client.host = client_host
    return request


@pytest.mark.asyncio
async def test_register_self_success():
    expected = {"agent_id": "my-agent-ab12cd", "api_key": "secret-key", "reputation": 0.3}

    with patch("app.routers.agents.get_pool", return_value=AsyncMock()), \
         patch("app.routers.agents.register_self_agent", AsyncMock(return_value=expected)) as register_mock:
        result = await register_self(AgentRegisterRequest(name="my-agent"), _make_request(client_host="203.0.113.5"))

    assert result.agent_id == "my-agent-ab12cd"
    assert result.api_key == "secret-key"
    assert result.reputation == 0.3
    assert register_mock.call_args[0][1:] == ("my-agent", "203.0.113.5")


@pytest.mark.asyncio
async def test_register_self_uses_x_forwarded_for():
    expected = {"agent_id": "a-1", "api_key": "k", "reputation": 0.3}

    with patch("app.routers.agents.get_pool", return_value=AsyncMock()), \
         patch("app.routers.agents.register_self_agent", AsyncMock(return_value=expected)) as register_mock:
        await register_self(
            AgentRegisterRequest(name="a"),
            _make_request(forwarded_for="198.51.100.9, 10.0.0.1", client_host="10.0.0.1"),
        )

    assert register_mock.call_args[0][1:] == ("a", "198.51.100.9")


@pytest.mark.asyncio
async def test_register_self_rate_limited():
    with patch("app.routers.agents.get_pool", return_value=AsyncMock()), \
         patch("app.routers.agents.register_self_agent", AsyncMock(side_effect=RateLimitExceeded(5, 86400))):
        with pytest.raises(HTTPException) as exc_info:
            await register_self(AgentRegisterRequest(name="a"), _make_request(client_host="203.0.113.5"))

    assert exc_info.value.status_code == 429
