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
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid API key"):
            await get_world_state(api_key="bad-key")


@pytest.mark.asyncio
async def test_get_world_state_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await get_world_state(api_key="key")


from app.mcp_server import propose_vision


@pytest.mark.asyncio
async def test_propose_vision_success():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_redis", lambda: AsyncMock()), \
         patch("app.mcp_server.check_duplicate", AsyncMock(return_value=False)), \
         patch("app.mcp_server.insert_pending_event", AsyncMock(return_value=1)), \
         patch("app.mcp_server.increment_submitted", AsyncMock()), \
         patch("app.mcp_server.publish", AsyncMock()):
        result = await propose_vision(
            api_key="key",
            description="build a server",
            ops=[{"op": "set", "key": "world.status", "value": "building"}],
        )

    assert result["status"] == "queued"
    assert "event_id" in result
    assert "submitted_at" in result


@pytest.mark.asyncio
async def test_propose_vision_invalid_api_key():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid API key"):
            await propose_vision(api_key="bad", description="x", ops=[{"op": "set", "key": "a", "value": 1}])


@pytest.mark.asyncio
async def test_propose_vision_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(30, 60))), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await propose_vision(api_key="key", description="x", ops=[{"op": "set", "key": "a", "value": 1}])


@pytest.mark.asyncio
async def test_propose_vision_payload_too_large():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="payload too large"):
            await propose_vision(
                api_key="key",
                description="big payload",
                ops=[{"op": "set", "key": "blob", "value": "x" * 9000}],
            )


@pytest.mark.asyncio
async def test_propose_vision_duplicate():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_redis", lambda: AsyncMock()), \
         patch("app.mcp_server.check_duplicate", AsyncMock(return_value=True)):
        with pytest.raises(ValueError, match="duplicate event"):
            await propose_vision(api_key="key", description="x", ops=[{"op": "set", "key": "a", "value": 1}])


@pytest.mark.asyncio
async def test_propose_vision_invalid_ops():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError):
            await propose_vision(api_key="key", description="x", ops=[{"op": "explode", "key": "a", "value": 1}])


from app.mcp_server import simulate_action
from app.schemas import SimulateOpResult


@pytest.mark.asyncio
async def test_simulate_action_success():
    results = [SimulateOpResult(key="world.status", op="set", before=None, after="building")]

    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_redis", lambda: AsyncMock()), \
         patch("app.mcp_server.simulate_ops", AsyncMock(return_value=(results, True, ["simulation valid"]))):
        result = await simulate_action(
            api_key="key",
            description="build a server",
            ops=[{"op": "set", "key": "world.status", "value": "building"}],
        )

    assert result["valid"] is True
    assert result["drift"] == 0.0
    assert result["results"][0]["key"] == "world.status"
    assert result["results"][0]["after"] == "building"


@pytest.mark.asyncio
async def test_simulate_action_invalid_api_key():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid API key"):
            await simulate_action(api_key="bad", description="x", ops=[{"op": "set", "key": "a", "value": 1}])


@pytest.mark.asyncio
async def test_simulate_action_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await simulate_action(api_key="key", description="x", ops=[{"op": "set", "key": "a", "value": 1}])


@pytest.mark.asyncio
async def test_simulate_action_invalid_ops():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError):
            await simulate_action(api_key="key", description="x", ops=[{"op": "explode", "key": "a", "value": 1}])


from app.mcp_server import evaluate_vision


@pytest.mark.asyncio
async def test_evaluate_vision_success():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.evaluate", AsyncMock(return_value=(0.8, True, ["all checks passed"]))):
        result = await evaluate_vision(
            api_key="key",
            description="build a server",
            ops=[{"op": "set", "key": "world.status", "value": "building"}],
        )

    assert result == {"score": 0.8, "would_accept": True, "reasons": ["all checks passed"]}


@pytest.mark.asyncio
async def test_evaluate_vision_invalid_api_key():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid API key"):
            await evaluate_vision(api_key="bad", description="x", ops=[{"op": "set", "key": "a", "value": 1}])


@pytest.mark.asyncio
async def test_evaluate_vision_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await evaluate_vision(api_key="key", description="x", ops=[{"op": "set", "key": "a", "value": 1}])


@pytest.mark.asyncio
async def test_evaluate_vision_invalid_ops():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError):
            await evaluate_vision(api_key="key", description="x", ops=[{"op": "explode", "key": "a", "value": 1}])
