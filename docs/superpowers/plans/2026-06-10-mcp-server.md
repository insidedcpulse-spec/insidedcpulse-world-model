# MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a remote MCP server (5 tools) mounted at `/mcp` inside the existing
FastAPI app, reusing existing business logic, plus the project's first
backend pytest suite.

**Architecture:** Extract `resolve_agent` (security.py) and
`enforce_rate_limit`/`RateLimitExceeded` (rate_limit.py) as reusable
async helpers. Build `backend/app/mcp_server.py` (`FastMCP("InsideDCPulse")`)
with 5 `@mcp.tool()`s that call these helpers plus existing repo/validation
functions. Mount `mcp.streamable_http_app()` at `/` (last, after all routes)
and run `mcp.session_manager.run()` inside the existing lifespan. Add nginx
`location /mcp`. Deploy via the existing webhook auto-deploy (push to `main`).

**Tech Stack:** FastAPI, `mcp==1.9.1` (FastMCP, streamable HTTP), pytest +
pytest-asyncio + `unittest.mock.AsyncMock`, asyncpg, redis.asyncio.

---

## Task 1: Backend test infrastructure

**Files:**
- Create: `backend/requirements-dev.txt`
- Create: `backend/pytest.ini`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/test_smoke.py`

- [ ] **Step 1: Write the smoke test**

```python
# backend/tests/test_smoke.py
def test_app_importable():
    from app.main import app

    assert app.title == "InsideDCPulse"
```

- [ ] **Step 2: Create empty package marker**

```python
# backend/tests/__init__.py
```

- [ ] **Step 3: Create pytest config**

```ini
# backend/pytest.ini
[pytest]
pythonpath = .
asyncio_mode = auto
```

- [ ] **Step 4: Create dev requirements**

```
# backend/requirements-dev.txt
pytest==8.3.4
pytest-asyncio==0.25.2
```

- [ ] **Step 5: Run test to verify it fails (no venv yet)**

Run: `cd backend && python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt -r requirements-dev.txt && .venv/bin/python -m pytest tests/ -v`

Expected: command runs (venv created, deps installed) and
`test_app_importable` either PASSes already or FAILs with an import error
unrelated to test infra (e.g. missing `mcp` package — expected, added in
Task 9). If it fails for any other reason, fix `pytest.ini`/`requirements-dev.txt`
before continuing.

- [ ] **Step 6: Confirm green baseline**

Run: `cd backend && .venv/bin/python -m pytest tests/ -v`
Expected: `tests/test_smoke.py::test_app_importable PASSED`

- [ ] **Step 7: Commit**

```bash
cd /root/insidedcpulse-world-model
git add backend/requirements-dev.txt backend/pytest.ini backend/tests/__init__.py backend/tests/test_smoke.py
git commit -m "test: add backend pytest infrastructure"
```

---

## Task 2: Refactor security.py — extract `resolve_agent`

**Files:**
- Modify: `backend/app/security.py`
- Create: `backend/tests/test_security.py`

Current `backend/app/security.py:19-33`:

```python
async def get_current_agent(x_api_key: str = Header(...)) -> dict:
    pool = get_pool()
    key_hash = hash_api_key(x_api_key)
    row = await pool.fetchrow(
        """
        UPDATE agents SET last_seen_at = now()
        WHERE api_key_hash = $1
        RETURNING id, name, reputation, total_submitted, total_accepted, total_rejected
        """,
        key_hash,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")
    AGENT_REQUESTS_TOTAL.labels(agent_id=row["id"]).inc()
    return dict(row)
```

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_security.py
import pytest
from unittest.mock import AsyncMock

from app.security import resolve_agent

AGENT_ROW = {
    "id": "agent-1",
    "name": "Agent One",
    "reputation": 0.5,
    "total_submitted": 10,
    "total_accepted": 8,
    "total_rejected": 2,
}


@pytest.mark.asyncio
async def test_resolve_agent_found():
    pool = AsyncMock()
    pool.fetchrow.return_value = dict(AGENT_ROW)

    agent = await resolve_agent(pool, "some-api-key")

    assert agent == AGENT_ROW
    pool.fetchrow.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_agent_not_found():
    pool = AsyncMock()
    pool.fetchrow.return_value = None

    agent = await resolve_agent(pool, "bad-key")

    assert agent is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_security.py -v`
Expected: `ImportError: cannot import name 'resolve_agent' from 'app.security'`

- [ ] **Step 3: Implement `resolve_agent` and refactor `get_current_agent`**

Replace `backend/app/security.py:19-33` with:

```python
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
```

Add `import asyncpg` to the top of `backend/app/security.py` (alongside the
existing `import hashlib` / `import secrets` lines).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_security.py -v`
Expected: both tests PASS

- [ ] **Step 5: Run full suite to confirm no regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/security.py backend/tests/test_security.py
git commit -m "refactor: extract resolve_agent from get_current_agent"
```

---

## Task 3: Refactor rate_limit.py — extract `enforce_rate_limit` / `RateLimitExceeded`

**Files:**
- Modify: `backend/app/rate_limit.py`
- Create: `backend/tests/test_rate_limit.py`

Current `backend/app/rate_limit.py` (full file, 32 lines):

```python
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
```

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_rate_limit.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_rate_limit.py -v`
Expected: `ImportError: cannot import name 'RateLimitExceeded' from 'app.rate_limit'`

- [ ] **Step 3: Implement `RateLimitExceeded`, `enforce_rate_limit`, refactor `rate_limited`**

Replace the full content of `backend/app/rate_limit.py` with:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_rate_limit.py -v`
Expected: both tests PASS

- [ ] **Step 5: Run full suite to confirm no regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/rate_limit.py backend/tests/test_rate_limit.py
git commit -m "refactor: extract enforce_rate_limit and RateLimitExceeded"
```

---

## Task 4: mcp_server.py — skeleton, `_authenticate`, `get_world_state`

**Files:**
- Create: `backend/app/mcp_server.py`
- Create: `backend/tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mcp_server.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: `ModuleNotFoundError: No module named 'app.mcp_server'`

- [ ] **Step 3: Implement the skeleton and `get_world_state`**

```python
# backend/app/mcp_server.py
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
```

- [ ] **Step 4: Install the `mcp` package (needed for import)**

Run: `cd backend && .venv/bin/pip install -q mcp==1.9.1`

(This is a temporary local install for test runs in this task. Task 9 adds
`mcp==1.9.1` to `requirements.txt` for the production image.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: all 3 tests PASS

- [ ] **Step 6: Run full suite to confirm no regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/mcp_server.py backend/tests/test_mcp_server.py
git commit -m "feat: add MCP server skeleton with get_world_state tool"
```

---

## Task 5: mcp_server.py — `propose_vision`

**Files:**
- Modify: `backend/app/mcp_server.py`
- Modify: `backend/tests/test_mcp_server.py`

- [ ] **Step 1: Append the failing tests**

Append to `backend/tests/test_mcp_server.py`:

```python
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
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="invalid API key"):
            await propose_vision(api_key="bad", description="x", ops=[{"op": "set", "key": "a", "value": 1}])


@pytest.mark.asyncio
async def test_propose_vision_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(30, 60))):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await propose_vision(api_key="key", description="x", ops=[{"op": "set", "key": "a", "value": 1}])


@pytest.mark.asyncio
async def test_propose_vision_payload_too_large():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()):
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
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()):
        with pytest.raises(ValueError):
            await propose_vision(api_key="key", description="x", ops=[{"op": "explode", "key": "a", "value": 1}])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: `ImportError: cannot import name 'propose_vision' from 'app.mcp_server'`

- [ ] **Step 3: Implement `propose_vision`**

Add these imports to the top of `backend/app/mcp_server.py` (after the
existing `from mcp.server.fastmcp import FastMCP` line):

```python
import json
import uuid
from datetime import datetime, timezone

from app.agents_repo import increment_submitted
from app.events_repo import insert_pending_event
from app.metrics import POSTGRES_WRITE_DURATION
from app.redis_client import get_redis
from app.schemas import VisionRequest
from app.validation import check_duplicate, estimate_size
from app.worker import publish
```

Append to `backend/app/mcp_server.py`:

```python
@mcp.tool()
async def propose_vision(
    api_key: str,
    description: str,
    ops: list[dict],
    event_type: str = "vision",
    metadata: dict | None = None,
) -> dict:
    """Propose a vision/action. Queued for deterministic validation, never applied directly."""
    agent = await _authenticate(api_key, WRITE)
    payload = VisionRequest(event_type=event_type, description=description, ops=ops, metadata=metadata or {})

    pool = get_pool()
    r = get_redis()

    size = estimate_size(payload)
    if size > settings.max_payload_bytes:
        raise ValueError(f"payload too large ({size} > {settings.max_payload_bytes} bytes)")

    if await check_duplicate(r, agent["id"], payload):
        raise ValueError("duplicate event: identical submission within the last 60s")

    event_id = uuid.uuid4()
    payload_dict = payload.model_dump(mode="json")

    with POSTGRES_WRITE_DURATION.time():
        await insert_pending_event(pool, event_id, agent["id"], payload.event_type, payload_dict)
        await increment_submitted(pool, agent["id"])

    await r.rpush(settings.queue_key, json.dumps({
        "event_id": str(event_id),
        "agent_id": agent["id"],
        "event_type": payload.event_type,
        "payload": payload_dict,
    }))

    await publish(r, {"type": "vision_received", "event_id": str(event_id), "agent_id": agent["id"]})

    return {"event_id": str(event_id), "status": "queued", "submitted_at": datetime.now(timezone.utc).isoformat()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: all tests PASS

- [ ] **Step 5: Run full suite to confirm no regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/mcp_server.py backend/tests/test_mcp_server.py
git commit -m "feat: add propose_vision MCP tool"
```

---

## Task 6: mcp_server.py — `simulate_action`

**Files:**
- Modify: `backend/app/mcp_server.py`
- Modify: `backend/tests/test_mcp_server.py`

- [ ] **Step 1: Append the failing tests**

Append to `backend/tests/test_mcp_server.py`:

```python
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
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="invalid API key"):
            await simulate_action(api_key="bad", description="x", ops=[{"op": "set", "key": "a", "value": 1}])


@pytest.mark.asyncio
async def test_simulate_action_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await simulate_action(api_key="key", description="x", ops=[{"op": "set", "key": "a", "value": 1}])


@pytest.mark.asyncio
async def test_simulate_action_invalid_ops():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()):
        with pytest.raises(ValueError):
            await simulate_action(api_key="key", description="x", ops=[{"op": "explode", "key": "a", "value": 1}])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: `ImportError: cannot import name 'simulate_action' from 'app.mcp_server'`

- [ ] **Step 3: Implement `simulate_action`**

Update the `app.world_state` import line in `backend/app/mcp_server.py` from:

```python
from app.world_state import get_state
```

to:

```python
from app.world_state import get_state, simulate_ops
```

Update the `app.validation` import line from:

```python
from app.validation import check_duplicate, estimate_size
```

to:

```python
from app.validation import check_duplicate, estimate_size, ops_hash
```

Append to `backend/app/mcp_server.py`:

```python
@mcp.tool()
async def simulate_action(
    api_key: str,
    description: str,
    ops: list[dict],
    event_type: str = "vision",
    metadata: dict | None = None,
) -> dict:
    """Dry-run ops against current world state. Nothing is persisted."""
    agent = await _authenticate(api_key, READ)
    payload = VisionRequest(event_type=event_type, description=description, ops=ops, metadata=metadata or {})

    pool = get_pool()
    r = get_redis()

    results, valid, reasons = await simulate_ops(pool, payload.ops)

    predicted = {res.key: res.after for res in results}
    sim_key = f"sim:{agent['id']}:{ops_hash(payload.ops)}"
    await r.set(sim_key, json.dumps(predicted, default=str), ex=300)

    return {
        "valid": valid,
        "reasons": reasons,
        "results": [res.model_dump(mode="json") for res in results],
        "drift": 0.0,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: all tests PASS

- [ ] **Step 5: Run full suite to confirm no regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/mcp_server.py backend/tests/test_mcp_server.py
git commit -m "feat: add simulate_action MCP tool"
```

---

## Task 7: mcp_server.py — `evaluate_vision`

**Files:**
- Modify: `backend/app/mcp_server.py`
- Modify: `backend/tests/test_mcp_server.py`

- [ ] **Step 1: Append the failing tests**

Append to `backend/tests/test_mcp_server.py`:

```python
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
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="invalid API key"):
            await evaluate_vision(api_key="bad", description="x", ops=[{"op": "set", "key": "a", "value": 1}])


@pytest.mark.asyncio
async def test_evaluate_vision_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await evaluate_vision(api_key="key", description="x", ops=[{"op": "set", "key": "a", "value": 1}])


@pytest.mark.asyncio
async def test_evaluate_vision_invalid_ops():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock()):
        with pytest.raises(ValueError):
            await evaluate_vision(api_key="key", description="x", ops=[{"op": "explode", "key": "a", "value": 1}])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: `ImportError: cannot import name 'evaluate_vision' from 'app.mcp_server'`

- [ ] **Step 3: Implement `evaluate_vision`**

Update the `app.validation` import line in `backend/app/mcp_server.py` from:

```python
from app.validation import check_duplicate, estimate_size, ops_hash
```

to:

```python
from app.validation import check_duplicate, estimate_size, evaluate, ops_hash
```

Append to `backend/app/mcp_server.py`:

```python
@mcp.tool()
async def evaluate_vision(
    api_key: str,
    description: str,
    ops: list[dict],
    event_type: str = "vision",
    metadata: dict | None = None,
) -> dict:
    """Score a vision against deterministic validation rules without queueing it."""
    agent = await _authenticate(api_key, READ)
    payload = VisionRequest(event_type=event_type, description=description, ops=ops, metadata=metadata or {})
    score, would_accept, reasons = await evaluate(get_pool(), agent, payload)
    return {"score": score, "would_accept": would_accept, "reasons": reasons}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: all tests PASS

- [ ] **Step 5: Run full suite to confirm no regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/mcp_server.py backend/tests/test_mcp_server.py
git commit -m "feat: add evaluate_vision MCP tool"
```

---

## Task 8: mcp_server.py — `get_world_memory`

**Files:**
- Modify: `backend/app/mcp_server.py`
- Modify: `backend/tests/test_mcp_server.py`

- [ ] **Step 1: Append the failing tests**

Append to `backend/tests/test_mcp_server.py`:

```python
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
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="invalid API key"):
            await get_world_memory(api_key="bad")


@pytest.mark.asyncio
async def test_get_world_memory_rate_limited():
    with patch("app.mcp_server.resolve_agent", AsyncMock(return_value=AGENT)), \
         patch("app.mcp_server.enforce_rate_limit", AsyncMock(side_effect=RateLimitExceeded(120, 60))):
        with pytest.raises(ValueError, match="rate limit exceeded"):
            await get_world_memory(api_key="key")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: `ImportError: cannot import name 'get_world_memory' from 'app.mcp_server'`

- [ ] **Step 3: Implement `get_world_memory`**

Add `get_memory` to the `app.events_repo` import. The current import line is:

```python
from app.events_repo import insert_pending_event
```

Change it to:

```python
from app.events_repo import get_memory, insert_pending_event
```

Append to `backend/app/mcp_server.py`:

```python
@mcp.tool()
async def get_world_memory(
    api_key: str,
    limit: int = 50,
    offset: int = 0,
    agent_id: str | None = None,
    status: str | None = None,
) -> dict:
    """Paginated, filterable event log (the audit trail)."""
    await _authenticate(api_key, READ)
    result = await get_memory(get_pool(), limit, offset, agent_id, status)
    return result.model_dump(mode="json")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: all tests PASS (5 tools x ~3-6 tests each)

- [ ] **Step 5: Run full suite to confirm no regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/mcp_server.py backend/tests/test_mcp_server.py
git commit -m "feat: add get_world_memory MCP tool"
```

---

## Task 9: Mount MCP server in main.py + requirements.txt

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_mcp_mount.py`

- [ ] **Step 1: Add `mcp` to production requirements**

Append to `backend/requirements.txt`:

```
mcp==1.9.1
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_mcp_mount.py
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def test_mcp_mounted_and_root_routes_not_shadowed():
    with patch("app.main.init_pool", AsyncMock(return_value=AsyncMock())), \
         patch("app.main.close_pool", AsyncMock()), \
         patch("app.main.get_redis", return_value=AsyncMock()), \
         patch("app.main.close_redis", AsyncMock()), \
         patch("app.main.worker_loop", AsyncMock()):
        from app.main import app

        with TestClient(app) as client:
            r = client.get("/")
            assert r.status_code == 200
            assert r.json()["name"] == "InsideDCPulse"

            r = client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
            )
            assert r.status_code == 200
            assert "InsideDCPulse" in r.text
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_mount.py -v`
Expected: FAIL — `POST /mcp` returns 404 (not mounted yet)

- [ ] **Step 4: Modify main.py — import mcp, run session manager, mount**

In `backend/app/main.py`, add this import alongside the existing imports
(after `from app.metrics import ...`):

```python
from app.mcp_server import mcp
```

Replace the `lifespan` function body (`backend/app/main.py:17-35`) — change
the bare `yield` into the session-manager context manager:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await init_pool()
    redis_client = get_redis()

    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(worker_loop(pool, redis_client, stop_event))
    logger.info("InsideDCPulse API ready")

    async with mcp.session_manager.run():
        yield

    stop_event.set()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    await close_redis()
    await close_pool()
```

At the very end of `backend/app/main.py` (after the `root()` route, currently
the last lines), add:

```python
# Mounted last so it only matches paths not already handled by the routes above.
app.mount("/", mcp.streamable_http_app(), name="mcp")
```

- [ ] **Step 5: Install mcp into the dev venv (if not already from Task 4)**

Run: `cd backend && .venv/bin/pip install -q mcp==1.9.1`

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_mount.py -v`
Expected: PASS

- [ ] **Step 7: Run full suite to confirm no regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add backend/requirements.txt backend/app/main.py backend/tests/test_mcp_mount.py
git commit -m "feat: mount MCP streamable HTTP server at /mcp"
```

---

## Task 10: nginx — `location /mcp`

**Files:**
- Modify: `docker/nginx/conf.d/insidedcpulse.conf.ssl`

- [ ] **Step 1: Add the `/mcp` location block**

In `docker/nginx/conf.d/insidedcpulse.conf.ssl`, insert the following block
immediately before the `# Public API` / `location /` block (currently
`docker/nginx/conf.d/insidedcpulse.conf.ssl:71-80`):

```nginx
    # MCP server (streamable HTTP — long-lived, unbuffered)
    location /mcp {
        proxy_pass http://api:8000/mcp;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }

```

So the file reads, around that area:

```nginx
    # MCP server (streamable HTTP — long-lived, unbuffered)
    location /mcp {
        proxy_pass http://api:8000/mcp;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }

    # Public API
    location / {
        limit_req zone=api_limit burst=40 nodelay;
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}
```

- [ ] **Step 2: Validate nginx syntax**

Run: `cd /root/insidedcpulse-world-model && docker run --rm -v "$(pwd)/docker/nginx/conf.d/insidedcpulse.conf.ssl:/etc/nginx/conf.d/insidedcpulse.conf:ro" nginx:alpine nginx -t -c /etc/nginx/nginx.conf 2>&1 | tail -5`

Expected: `nginx: configuration file /etc/nginx/nginx.conf test is successful`
(it may also print warnings about the missing `ssl_certificate` files from
other server blocks not present in this minimal test — only check there is
no syntax error reported on the lines you added).

If the generic `nginx -t` invocation above complains only about missing
cert/conf files unrelated to the new block, that's expected (this file
references certs that only exist on the VPS) — proceed.

- [ ] **Step 3: Commit**

```bash
git add docker/nginx/conf.d/insidedcpulse.conf.ssl
git commit -m "feat: proxy /mcp to the FastAPI MCP server"
```

---

## Task 11: Deploy and verify end-to-end

**Files:** none (deploy + manual verification only)

- [ ] **Step 1: Push to main (triggers webhook auto-deploy)**

```bash
cd /root/insidedcpulse-world-model
git push origin main
```

If `git push` fails with `401`/`Bad credentials`, the GitHub PAT has expired
again (known issue, see project memory `project_insidedcpulse.md`). Ask the
user for a fresh fine-grained PAT (Contents: Read & write), verify it
immediately with:

```bash
curl -s -H "Authorization: Bearer $TOK" https://api.github.com/user
```

then `export GITHUB_TOKEN=$TOK` and retry `git push origin main` right away.

- [ ] **Step 2: Wait for the webhook deploy to complete**

The webhook (`insidedcpulse-webhook` systemd service on the VPS) runs
`git fetch/reset --hard` + `docker compose build api` + `docker compose up -d`
on push to `main`. This typically takes 30-90s. Then check:

```bash
ssh -i /root/.ssh/insidedcpulse_deploy root@2.25.169.27 \
  "journalctl -u insidedcpulse-webhook -n 20 --no-pager"
```

Expected: log shows `[deploy] done` for the latest commit SHA.

- [ ] **Step 3: Confirm the API is healthy**

```bash
curl -s https://insidedcpulse.com/healthz
```

Expected: `{"status":"ok"}`

- [ ] **Step 4: Confirm the MCP endpoint handshake**

```bash
curl -s -i https://insidedcpulse.com/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"verify","version":"1"}}}'
```

Expected: `HTTP/2 200`, `content-type: text/event-stream`, body contains
`"InsideDCPulse"`. (Not `404`/`502`.)

- [ ] **Step 5: Confirm `get_world_state` matches the REST API shape**

Get a real agent API key from `/root/insidedcpulse-secrets/vps.env` or by
registering a test agent via `POST /api/v1/agents/register` (admin key
required), then compare:

```bash
curl -s https://insidedcpulse.com/api/v1/world/state -H "X-API-Key: <agent-key>"
```

against an MCP `tools/call` request for `get_world_state` with the same
`api_key` argument (via an MCP client, or `mcp dev` inspector pointed at
`https://insidedcpulse.com/mcp`). Expected: same `state`/`as_of` shape.

- [ ] **Step 6: Confirm invalid API key produces an MCP error result**

Using an MCP client / inspector, call any tool (e.g. `get_world_state`) with
`api_key="invalid"`. Expected: MCP `isError: true` result containing
`"invalid API key"` (HTTP-level response is still 200 — errors are inside the
MCP protocol payload, per the design's error-handling table).

- [ ] **Step 7: Update project memory**

Update `/root/.claude/projects/-root/memory/project_insidedcpulse.md` and
`MEMORY.md` to record that the MCP server is live at
`https://insidedcpulse.com/mcp` with 5 tools, deployed via the webhook.

---

## Self-Review Notes

- **Spec coverage:** all 5 tools (`get_world_state`, `propose_vision`,
  `simulate_action`, `evaluate_vision`, `get_world_memory`), `resolve_agent`
  refactor, `enforce_rate_limit`/`RateLimitExceeded` refactor, `mcp==1.9.1`
  dependency, `main.py` mount + lifespan, nginx `/mcp` location, first
  pytest suite (`test_security.py`, `test_rate_limit.py`,
  `test_mcp_server.py`, `test_mcp_mount.py`), `requirements-dev.txt` — all
  covered across Tasks 1-10. Manual e2e verification covered in Task 11.
- **Out of scope (per spec, intentionally not planned):** OAuth/MCP auth
  spec, new MCP resources/prompts, exposing `commit`/`agents/register` as
  tools, stdio MCP variant.
- **Type consistency:** `_authenticate(api_key, limit)` signature is
  identical across all 5 tool implementations (Tasks 4-8). `READ`/`WRITE`
  constants defined once in Task 4, reused by all later tasks. Import lines
  for `app.world_state` and `app.validation` are updated incrementally
  (Tasks 4, 6, 7) rather than redefined — each task's Step 3 specifies the
  exact before/after import line to avoid duplicate imports.
- **Unpushed commits:** the repo currently has 2 commits ahead of
  `origin/main` (Google Search Console verification, `010a8f6`/`6916a32`)
  plus the spec commit (`b6af139`). Task 11's `git push` will include these
  along with all MCP commits — this is intentional (one push, one deploy).
