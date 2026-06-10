import time

from fastapi import Depends, HTTPException, status

from app.config import settings
from app.redis_client import get_redis
from app.security import get_current_agent


def rate_limited(limit: int):
    """Per-agent fixed-window rate limiter backed by Redis.

    Returns a FastAPI dependency that yields the authenticated agent dict,
    so routes can depend on this alone instead of get_current_agent.
    """

    async def dependency(agent: dict = Depends(get_current_agent)) -> dict:
        r = get_redis()
        window = int(time.time() // settings.rate_limit_window_seconds)
        key = f"ratelimit:{agent['id']}:{window}"
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, settings.rate_limit_window_seconds)
        if count > limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"rate limit exceeded ({limit}/{settings.rate_limit_window_seconds}s)",
            )
        return agent

    return dependency
