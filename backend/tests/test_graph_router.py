from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.routers.graph import causal_chain, neighbors, node, path, timeline

AGENT = {"id": "agent-1"}


@pytest.mark.asyncio
async def test_node_found():
    fake = MagicMock()
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_node", AsyncMock(return_value=fake)):
        result = await node("service.checkout", agent=AGENT)

    assert result is fake


@pytest.mark.asyncio
async def test_node_not_found():
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_node", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await node("service.ghost", agent=AGENT)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_neighbors_found():
    fake = MagicMock()
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_neighbors", AsyncMock(return_value=fake)) as mock_fn:
        result = await neighbors("service.checkout", None, "both", 50, agent=AGENT)

    assert result is fake
    assert mock_fn.await_args.args[1:] == ("service.checkout", None, "both", 50)


@pytest.mark.asyncio
async def test_neighbors_not_found():
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_neighbors", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await neighbors("service.ghost", None, "both", 50, agent=AGENT)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_path_found():
    fake = MagicMock()
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_path", AsyncMock(return_value=fake)):
        result = await path(from_="incident.inc3", to="service.checkout", max_depth=6, agent=AGENT)

    assert result is fake


@pytest.mark.asyncio
async def test_path_node_not_found():
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_path", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await path(from_="service.ghost", to="service.checkout", max_depth=6, agent=AGENT)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_timeline_found():
    fake = MagicMock()
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_timeline", AsyncMock(return_value=fake)):
        result = await timeline(entity=None, limit=50, offset=0, agent=AGENT)

    assert result is fake


@pytest.mark.asyncio
async def test_timeline_entity_not_found():
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_timeline", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await timeline(entity="service.ghost", limit=50, offset=0, agent=AGENT)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_causal_chain_found():
    fake = MagicMock()
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_causal_edges", AsyncMock(return_value=fake)):
        result = await causal_chain(node="incident.inc3", direction="upstream", max_depth=3, agent=AGENT)

    assert result is fake


@pytest.mark.asyncio
async def test_causal_chain_not_found():
    with patch("app.routers.graph.get_pool", return_value=AsyncMock()), \
         patch("app.routers.graph.get_causal_edges", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await causal_chain(node="service.ghost", direction="upstream", max_depth=3, agent=AGENT)

    assert exc_info.value.status_code == 404
