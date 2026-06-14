import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.mcp_server import get_world_state, register_agent
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


from app.mcp_server import get_world_memory
from app.schemas import MemoryResponse


@pytest.mark.asyncio
async def test_get_world_memory_success():
    fake_memory = MemoryResponse(items=[], total=0, limit=50, offset=0)

    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_memory", AsyncMock(return_value=fake_memory)):
        result = await get_world_memory(api_key="key")

    assert result["total"] == 0
    assert result["limit"] == 50
    assert result["offset"] == 0
    assert result["items"] == []


@pytest.mark.asyncio
async def test_get_world_memory_invalid_api_key():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid API key"):
            await get_world_memory(api_key="bad")


@pytest.mark.asyncio
async def test_get_world_memory_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await get_world_memory(api_key="key")


@pytest.mark.asyncio
async def test_register_agent_success():
    expected = {"agent_id": "my-agent-ab12cd", "api_key": "secret-key", "reputation": 0.3}

    with patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_client_ip", return_value="203.0.113.5"), \
         patch("app.mcp_server.register_self_agent", AsyncMock(return_value=expected)) as register_mock:
        result = await register_agent(name="my-agent")

    assert result == expected
    assert register_mock.call_args[0][1:] == ("my-agent", "203.0.113.5")


@pytest.mark.asyncio
async def test_register_agent_rate_limited():
    with patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_client_ip", return_value="203.0.113.5"), \
         patch("app.mcp_server.register_self_agent", AsyncMock(side_effect=RateLimitExceeded(5, 86400))):
        with pytest.raises(ValueError):
            await register_agent(name="my-agent")


@pytest.mark.asyncio
async def test_register_agent_returned_key_resolves_to_self_serve_agent():
    from app.security import hash_api_key

    captured = {}

    async def fake_create_agent(pool, agent_id, name, api_key_hash, reputation, created_via):
        captured["hash"] = api_key_hash
        return {"id": agent_id, "name": name, "reputation": reputation, "created_via": created_via}

    with patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_client_ip", return_value="203.0.113.5"), \
         patch("app.agent_registration.enforce_ip_rate_limit", AsyncMock()), \
         patch("app.agent_registration.create_agent", AsyncMock(side_effect=fake_create_agent)):
        result = await register_agent(name="my-agent")

    assert hash_api_key(result["api_key"]) == captured["hash"]
    assert result["reputation"] == 0.3


from app.mcp_server import get_graph_node


@pytest.mark.asyncio
async def test_get_graph_node_success():
    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"node": {"id": "incident.inc3"}, "edges_out": [], "edges_in": []}

    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_node", AsyncMock(return_value=fake_result)):
        result = await get_graph_node(api_key="key", node_id="incident.inc3")

    assert result == {"node": {"id": "incident.inc3"}, "edges_out": [], "edges_in": []}


@pytest.mark.asyncio
async def test_get_graph_node_invalid_api_key():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid API key"):
            await get_graph_node(api_key="bad-key", node_id="incident.inc3")


@pytest.mark.asyncio
async def test_get_graph_node_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await get_graph_node(api_key="key", node_id="incident.inc3")


@pytest.mark.asyncio
async def test_get_graph_node_not_found():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_node", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="node not found: service.ghost"):
            await get_graph_node(api_key="key", node_id="service.ghost")


from app.mcp_server import get_graph_neighbors


@pytest.mark.asyncio
async def test_get_graph_neighbors_success():
    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"node_id": "service.checkout", "edge_type": None, "direction": "both", "neighbors": []}

    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_neighbors", AsyncMock(return_value=fake_result)):
        result = await get_graph_neighbors(api_key="key", node_id="service.checkout")

    assert result["node_id"] == "service.checkout"


@pytest.mark.asyncio
async def test_get_graph_neighbors_invalid_api_key():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid API key"):
            await get_graph_neighbors(api_key="bad-key", node_id="service.checkout")


@pytest.mark.asyncio
async def test_get_graph_neighbors_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await get_graph_neighbors(api_key="key", node_id="service.checkout")


@pytest.mark.asyncio
async def test_get_graph_neighbors_not_found():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_neighbors", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="node not found: service.ghost"):
            await get_graph_neighbors(api_key="key", node_id="service.ghost")


@pytest.mark.asyncio
async def test_get_graph_neighbors_invalid_direction():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid direction"):
            await get_graph_neighbors(api_key="key", node_id="service.checkout", direction="sideways")


@pytest.mark.asyncio
async def test_get_graph_neighbors_limit_out_of_range():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="limit must be between 1 and 200"):
            await get_graph_neighbors(api_key="key", node_id="service.checkout", limit=500)


from app.mcp_server import get_event_timeline


@pytest.mark.asyncio
async def test_get_event_timeline_success():
    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"entity": None, "events": []}

    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_timeline", AsyncMock(return_value=fake_result)):
        result = await get_event_timeline(api_key="key")

    assert result == {"entity": None, "events": []}


@pytest.mark.asyncio
async def test_get_event_timeline_invalid_api_key():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="invalid API key"):
            await get_event_timeline(api_key="bad-key")


@pytest.mark.asyncio
async def test_get_event_timeline_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await get_event_timeline(api_key="key")


@pytest.mark.asyncio
async def test_get_event_timeline_entity_not_found():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()), \
         patch("app.mcp_server.get_timeline", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="node not found: service.ghost"):
            await get_event_timeline(api_key="key", entity="service.ghost")


@pytest.mark.asyncio
async def test_get_event_timeline_limit_out_of_range():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()), \
         patch("app.mcp_server.get_pool", lambda: AsyncMock()):
        with pytest.raises(ValueError, match="limit must be between 1 and 200"):
            await get_event_timeline(api_key="key", limit=0)
