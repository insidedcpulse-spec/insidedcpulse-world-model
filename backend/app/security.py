import asyncpg
import hashlib
import secrets

from fastapi import Header, HTTPException, status

from app.config import settings
from app.database import get_pool
from app.metrics import AGENT_REQUESTS_TOTAL


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


async def resolve_agent(pool: asyncpg.Pool, api_key: str) -> dict | None:
    key_hash = hash_api_key(api_key)
    row = await pool.fetchrow(
        """
        UPDATE agents SET last_seen_at = now()
        WHERE api_key_hash = $1
        RETURNING id, name, reputation, total_submitted, total_accepted, total_rejected
        """,
        key_hash,
    )
    if row is None:
        return None
    AGENT_REQUESTS_TOTAL.labels(agent_id=row["id"]).inc()
    return dict(row)


async def get_current_agent(x_api_key: str = Header(...)) -> dict:
    pool = get_pool()
    agent = await resolve_agent(pool, x_api_key)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")
    return agent


async def require_internal_key(x_internal_key: str = Header(...)) -> None:
    if not secrets.compare_digest(x_internal_key, settings.internal_api_key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid internal key")


async def require_admin_key(x_admin_key: str = Header(...)) -> None:
    if not secrets.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid admin key")
