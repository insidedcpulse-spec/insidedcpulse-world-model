from mcp.server.fastmcp import FastMCP

from app.config import settings
from app.database import get_pool
from app.rate_limit import RateLimitExceeded, enforce_rate_limit
from app.security import resolve_agent
from app.world_state import get_state

mcp = FastMCP("InsideDCPulse")

READ = settings.rate_limit_read_per_window
WRITE = settings.rate_limit_vision_per_window


async def _authenticate(api_key: str, limit: int) -> dict:
    pool = get_pool()
    agent = await resolve_agent(pool, api_key)
    if agent is None:
        raise ValueError("invalid API key")
    try:
        await enforce_rate_limit(agent["id"], limit)
    except RateLimitExceeded as exc:
        raise ValueError(str(exc)) from exc
    return agent


@mcp.tool()
async def get_world_state(api_key: str) -> dict:
    """Return the current materialized world state."""
    await _authenticate(api_key, READ)
    state = await get_state(get_pool())
    return state.model_dump(mode="json")
