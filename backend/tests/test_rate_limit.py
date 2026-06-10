import pytest
from unittest.mock import AsyncMock, patch

from app.rate_limit import RateLimitExceeded, enforce_rate_limit


@pytest.mark.asyncio
async def test_enforce_rate_limit_under_limit():
    redis_mock = AsyncMock()
    redis_mock.incr.return_value = 1

    with patch("app.rate_limit.get_redis", return_value=redis_mock):
        await enforce_rate_limit("agent-1", limit=10)

    redis_mock.incr.assert_awaited_once()
    key = redis_mock.incr.call_args[0][0]
    assert key.startswith("ratelimit:agent-1:")
    redis_mock.expire.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_rate_limit_over_limit():
    redis_mock = AsyncMock()
    redis_mock.incr.return_value = 11

    with patch("app.rate_limit.get_redis", return_value=redis_mock):
        with pytest.raises(RateLimitExceeded) as exc_info:
            await enforce_rate_limit("agent-1", limit=10)

    assert exc_info.value.limit == 10
    assert exc_info.value.window == 60
