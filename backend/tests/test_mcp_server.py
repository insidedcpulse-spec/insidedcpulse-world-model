import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.mcp_server import get_world_state
from app.rate_limit import RateLimitExceeded

AGENT = {
    "id": "agent-1",
    "name": "Agent One",
    "reputation": 0.5,
    "total_submitted": 10,
    "total_accepted": 8,
    "total_rejected": 2,
}


@pytest.mark.asyncio
async def test_get_world_state_success():
    fake_state = MagicMock()
    fake_state.model_dump.return_value = {"state": {}, "as_of": "2026-06-10T00:00:00Z"}

    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_state", AsyncMock(return_value=fake_state)):
        result = await get_world_state(api_key="key")

    assert result == {"state": {}, "as_of": "2026-06-10T00:00:00Z"}


@pytest.mark.asyncio
async def test_get_world_state_invalid_api_key():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="invalid API key"):
            await get_world_state(api_key="bad-key")


@pytest.mark.asyncio
async def test_get_world_state_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await get_world_state(api_key="key")
