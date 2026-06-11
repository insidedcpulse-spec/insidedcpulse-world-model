import re
import secrets

import asyncpg

from app.agents_repo import create_agent
from app.config import settings
from app.rate_limit import enforce_ip_rate_limit
from app.security import generate_api_key, hash_api_key

SELF_SERVE_INITIAL_REPUTATION = 0.3


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "agent"


async def register_self_agent(pool: asyncpg.Pool, name: str, client_ip: str) -> dict:
    """Self-serve registration: rate-limit by IP, then create a low-reputation agent.

    Shared by the REST `/api/v1/agents/register-self` endpoint and the MCP
    `register_agent` tool — both resolve `client_ip` differently and call
    this with the same arguments.
    """
    await enforce_ip_rate_limit(
        client_ip,
        settings.rate_limit_register_per_window,
        settings.rate_limit_register_window_seconds,
    )

    agent_id = f"{_slugify(name)}-{secrets.token_hex(3)}"
    api_key = generate_api_key()
    agent = await create_agent(
        pool,
        agent_id,
        name,
        hash_api_key(api_key),
        reputation=SELF_SERVE_INITIAL_REPUTATION,
        created_via="self_serve",
    )
    return {"agent_id": agent["id"], "api_key": api_key, "reputation": float(agent["reputation"])}
