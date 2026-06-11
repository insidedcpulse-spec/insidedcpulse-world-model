import time

from fastapi import Depends, HTTPException, status

from app.config import settings
from app.redis_client import get_redis
from app.security import get_current_agent


class RateLimitExceeded(Exception):
    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        super().__init__(f"rate limit exceeded ({limit}/{window}s)")


async def enforce_rate_limit(agent_id: str, limit: int) -> None:
    r = get_redis()
    window = int(time.time() // settings.rate_limit_window_seconds)
    key = f"ratelimit:{agent_id}:{window}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, settings.rate_limit_window_seconds)
    if count > limit:
        raise RateLimitExceeded(limit, settings.rate_limit_window_seconds)


async def enforce_ip_rate_limit(ip: str, limit: int, window_seconds: int) -> None:
    r = get_redis()
    window = int(time.time() // window_seconds)
    key = f"ratelimit:register:{ip}:{window}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, window_seconds)
    if count > limit:
        raise RateLimitExceeded(limit, window_seconds)


def rate_limited(limit: int):
    """Per-agent fixed-window rate limiter backed by Redis.

    Returns a FastAPI dependency that yields the authenticated agent dict,
    so routes can depend on this alone instead of get_current_agent.
    """

    async def dependency(agent: dict = Depends(get_current_agent)) -> dict:
        try:
            await enforce_rate_limit(agent["id"], limit)
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
        return agent

    return dependency
