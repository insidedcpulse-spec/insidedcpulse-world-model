# Self-serve Agent Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any agent provision its own `agent_id` + `api_key` via
`POST /api/v1/agents/register-self` (REST) or `register_agent` (MCP tool),
with a 5/IP/24h rate limit and an initial reputation of `0.3`
(`created_via = "self_serve"`), removing the admin-key barrier described in
`docs/superpowers/specs/2026-06-11-self-serve-agent-registration-design.md`.

**Architecture:** A new shared module `backend/app/agent_registration.py`
holds `register_self_agent(pool, name, client_ip)`, which does IP rate-limit
check (new `enforce_ip_rate_limit` in `rate_limit.py`) → `create_agent(...,
reputation=0.3, created_via="self_serve")` (extended `agents_repo.py`) →
returns `{agent_id, api_key, reputation}`. The REST endpoint
(`routers/agents.py`) and MCP tool (`mcp_server.py`) are thin wrappers that
resolve `client_ip` differently — REST from `request`/`X-Forwarded-For`, MCP
from a `ContextVar` populated by `MCPMethodGuardMiddleware` (`mcp_guard.py`).
A new `agents.created_via` column is added to `init.sql` (fresh installs) and
applied to the live DB via a one-time manual `ALTER TABLE`.

**Tech Stack:** FastAPI, asyncpg, Redis (`redis.asyncio`), `mcp` (FastMCP
streamable HTTP), pytest + pytest-asyncio + `unittest.mock`.

---

### Task 1: `agents.created_via` column (DB schema)

**Files:**
- Modify: `docker/postgres/init.sql:4-14`

- [ ] **Step 1: Add the column to the `agents` table definition**

Old (lines 4-14):
```sql
CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    api_key_hash    TEXT NOT NULL UNIQUE,
    reputation      NUMERIC(5,4) NOT NULL DEFAULT 0.5000,
    total_submitted BIGINT NOT NULL DEFAULT 0,
    total_accepted  BIGINT NOT NULL DEFAULT 0,
    total_rejected  BIGINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ
);
```

New:
```sql
CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    api_key_hash    TEXT NOT NULL UNIQUE,
    reputation      NUMERIC(5,4) NOT NULL DEFAULT 0.5000,
    total_submitted BIGINT NOT NULL DEFAULT 0,
    total_accepted  BIGINT NOT NULL DEFAULT 0,
    total_rejected  BIGINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ,
    created_via     TEXT NOT NULL DEFAULT 'admin'
);
```

- [ ] **Step 2: Verify**

Run: `grep -n "created_via" docker/postgres/init.sql`
Expected: one match, `created_via     TEXT NOT NULL DEFAULT 'admin'` inside
the `agents` table block.

- [ ] **Step 3: Commit**

```bash
git add docker/postgres/init.sql
git commit -m "Add created_via column to agents table schema"
```

---

### Task 2: `create_agent` gains `reputation`/`created_via` params

**Files:**
- Modify: `backend/app/agents_repo.py:6-15`
- Test: `backend/tests/test_agents_repo.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_agents_repo.py`:

```python
import pytest
from unittest.mock import AsyncMock

from app.agents_repo import create_agent


@pytest.mark.asyncio
async def test_create_agent_admin_defaults():
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "id": "agent-y-ab12cd",
        "name": "agent-y",
        "reputation": 0.5,
        "created_via": "admin",
    }

    agent = await create_agent(pool, "agent-y-ab12cd", "agent-y", "hash456")

    assert agent["reputation"] == 0.5
    assert agent["created_via"] == "admin"

    args = pool.fetchrow.call_args[0]
    assert args[1:] == ("agent-y-ab12cd", "agent-y", "hash456", 0.5, "admin")


@pytest.mark.asyncio
async def test_create_agent_self_serve():
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "id": "agent-x-ab12cd",
        "name": "agent-x",
        "reputation": 0.3,
        "created_via": "self_serve",
    }

    agent = await create_agent(
        pool, "agent-x-ab12cd", "agent-x", "hash123",
        reputation=0.3, created_via="self_serve",
    )

    assert agent["reputation"] == 0.3
    assert agent["created_via"] == "self_serve"

    args = pool.fetchrow.call_args[0]
    assert args[1:] == ("agent-x-ab12cd", "agent-x", "hash123", 0.3, "self_serve")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_agents_repo.py -v`
Expected: FAIL — `create_agent() got an unexpected keyword argument
'reputation'` (and the admin-defaults test fails on `args[1:]` length
mismatch).

- [ ] **Step 3: Extend `create_agent`**

In `backend/app/agents_repo.py`, replace the `create_agent` function (lines
6-15):

Old:
```python
async def create_agent(pool: asyncpg.Pool, agent_id: str, name: str, api_key_hash: str) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO agents (id, name, api_key_hash)
        VALUES ($1, $2, $3)
        RETURNING id, name, reputation
        """,
        agent_id, name, api_key_hash,
    )
    return dict(row)
```

New:
```python
async def create_agent(
    pool: asyncpg.Pool,
    agent_id: str,
    name: str,
    api_key_hash: str,
    reputation: float = 0.5,
    created_via: str = "admin",
) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO agents (id, name, api_key_hash, reputation, created_via)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, name, reputation, created_via
        """,
        agent_id, name, api_key_hash, reputation, created_via,
    )
    return dict(row)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_agents_repo.py -v`
Expected: `2 passed`.

- [ ] **Step 5: Run full suite to confirm nothing else broke**

Run: `cd backend && .venv/bin/pytest -q`
Expected: `82 passed` (existing 80 + 2 new). The admin `/register` handler
calls `create_agent(pool, agent_id, payload.name, hash_api_key(api_key))`
with no new args, so it keeps using the new defaults
(`reputation=0.5, created_via="admin"`) — unchanged behavior.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents_repo.py backend/tests/test_agents_repo.py
git commit -m "Add reputation and created_via params to create_agent"
```

---

### Task 3: `enforce_ip_rate_limit` helper

**Files:**
- Modify: `backend/app/rate_limit.py`
- Test: `backend/tests/test_rate_limit.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_rate_limit.py`:

```python
from app.rate_limit import enforce_ip_rate_limit


@pytest.mark.asyncio
async def test_enforce_ip_rate_limit_under_limit():
    redis_mock = AsyncMock()
    redis_mock.incr.return_value = 1

    with patch("app.rate_limit.get_redis", return_value=redis_mock):
        await enforce_ip_rate_limit("203.0.113.5", limit=5, window_seconds=86400)

    redis_mock.incr.assert_awaited_once()
    key = redis_mock.incr.call_args[0][0]
    assert key.startswith("ratelimit:register:203.0.113.5:")
    redis_mock.expire.assert_awaited_once_with(key, 86400)


@pytest.mark.asyncio
async def test_enforce_ip_rate_limit_over_limit():
    redis_mock = AsyncMock()
    redis_mock.incr.return_value = 6

    with patch("app.rate_limit.get_redis", return_value=redis_mock):
        with pytest.raises(RateLimitExceeded) as exc_info:
            await enforce_ip_rate_limit("203.0.113.5", limit=5, window_seconds=86400)

    assert exc_info.value.limit == 5
    assert exc_info.value.window == 86400
```

(The file already imports `pytest`, `AsyncMock`, `patch`, and
`RateLimitExceeded` — no new imports needed beyond `enforce_ip_rate_limit`
itself.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_rate_limit.py -v`
Expected: FAIL — `ImportError: cannot import name 'enforce_ip_rate_limit'`.

- [ ] **Step 3: Implement `enforce_ip_rate_limit`**

In `backend/app/rate_limit.py`, add this function after `enforce_rate_limit`
(after line 18, before `def rate_limited`):

```python
async def enforce_ip_rate_limit(ip: str, limit: int, window_seconds: int) -> None:
    r = get_redis()
    window = int(time.time() // window_seconds)
    key = f"ratelimit:register:{ip}:{window}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, window_seconds)
    if count > limit:
        raise RateLimitExceeded(limit, window_seconds)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_rate_limit.py -v`
Expected: `4 passed` (2 existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/rate_limit.py backend/tests/test_rate_limit.py
git commit -m "Add enforce_ip_rate_limit helper for registration rate limiting"
```

---

### Task 4: `mcp_guard` client-IP ContextVar

**Files:**
- Modify: `backend/app/mcp_guard.py`
- Test: `backend/tests/test_mcp_guard.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_mcp_guard.py`:

```python
from app.mcp_guard import _client_ip_var, _extract_client_ip, get_client_ip


def test_extract_client_ip_from_x_forwarded_for():
    scope = {
        "headers": [(b"x-forwarded-for", b"203.0.113.5, 10.0.0.1")],
        "client": ("10.0.0.1", 12345),
    }
    assert _extract_client_ip(scope) == "203.0.113.5"


def test_extract_client_ip_falls_back_to_scope_client():
    scope = {"headers": [], "client": ("198.51.100.7", 54321)}
    assert _extract_client_ip(scope) == "198.51.100.7"


def test_extract_client_ip_unknown_when_nothing_available():
    scope = {"headers": [], "client": None}
    assert _extract_client_ip(scope) == "unknown"


def test_get_client_ip_default_is_unknown():
    token = _client_ip_var.set("unknown")
    try:
        assert get_client_ip() == "unknown"
    finally:
        _client_ip_var.reset(token)


def test_get_client_ip_reads_contextvar():
    token = _client_ip_var.set("203.0.113.5")
    try:
        assert get_client_ip() == "203.0.113.5"
    finally:
        _client_ip_var.reset(token)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_mcp_guard.py -v`
Expected: FAIL — `ImportError: cannot import name '_client_ip_var'`.

- [ ] **Step 3: Add the ContextVar, accessor, and extractor**

In `backend/app/mcp_guard.py`, add `from contextvars import ContextVar` to
the imports (after `import logging`):

Old:
```python
import json
import logging

from starlette.types import ASGIApp, Receive, Scope, Send
```

New:
```python
import json
import logging
from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send
```

Then, after the `logger = logging.getLogger("insidedcpulse")` line, add:

```python
_client_ip_var: ContextVar[str] = ContextVar("client_ip", default="unknown")


def get_client_ip() -> str:
    """Return the client IP captured for the current /mcp request."""
    return _client_ip_var.get()


def _extract_client_ip(scope: Scope) -> str:
    headers = dict(scope.get("headers") or [])
    forwarded = headers.get(b"x-forwarded-for")
    if forwarded:
        return forwarded.decode("latin-1").split(",")[0].strip()
    client = scope.get("client")
    if client:
        return client[0]
    return "unknown"
```

- [ ] **Step 4: Set the ContextVar in `__call__`**

In `MCPMethodGuardMiddleware.__call__`, set the IP for every HTTP request
before the existing POST-only early return:

Old:
```python
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
```

New:
```python
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            _client_ip_var.set(_extract_client_ip(scope))

        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_mcp_guard.py -v`
Expected: `5 passed`.

- [ ] **Step 6: Run the MCP mount test to confirm the middleware still works**

Run: `cd backend && .venv/bin/pytest tests/test_mcp_mount.py -v`
Expected: `1 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/mcp_guard.py backend/tests/test_mcp_guard.py
git commit -m "Capture client IP via ContextVar in MCPMethodGuardMiddleware"
```

---

### Task 5: `agent_registration.register_self_agent` (shared logic)

**Files:**
- Modify: `backend/app/config.py`
- Create: `backend/app/agent_registration.py`
- Test: `backend/tests/test_agent_registration.py` (new)

- [ ] **Step 1: Add registration rate-limit settings**

In `backend/app/config.py`, extend the rate limiting block:

Old:
```python
    # Rate limiting (requests per window, per agent)
    rate_limit_window_seconds: int = 60
    rate_limit_vision_per_window: int = 30
    rate_limit_read_per_window: int = 120
```

New:
```python
    # Rate limiting (requests per window, per agent)
    rate_limit_window_seconds: int = 60
    rate_limit_vision_per_window: int = 30
    rate_limit_read_per_window: int = 120

    # Self-serve registration rate limit (per IP)
    rate_limit_register_per_window: int = 5
    rate_limit_register_window_seconds: int = 86400
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_agent_registration.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

from app.agent_registration import SELF_SERVE_INITIAL_REPUTATION, register_self_agent
from app.rate_limit import RateLimitExceeded
from app.security import hash_api_key


@pytest.mark.asyncio
async def test_register_self_agent_success():
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "id": "my-agent-ab12cd",
        "name": "my-agent",
        "reputation": 0.3,
        "created_via": "self_serve",
    }

    with patch("app.agent_registration.enforce_ip_rate_limit", AsyncMock()) as enforce_mock:
        result = await register_self_agent(pool, "my-agent", "203.0.113.5")

    enforce_mock.assert_awaited_once_with("203.0.113.5", 5, 86400)
    assert result["agent_id"] == "my-agent-ab12cd"
    assert result["reputation"] == SELF_SERVE_INITIAL_REPUTATION == 0.3

    insert_args = pool.fetchrow.call_args[0]
    assert insert_args[3] == hash_api_key(result["api_key"])
    assert insert_args[4] == 0.3
    assert insert_args[5] == "self_serve"


@pytest.mark.asyncio
async def test_register_self_agent_slugifies_name():
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "id": "my-cool-agent-ab12cd",
        "name": "My Cool Agent!",
        "reputation": 0.3,
        "created_via": "self_serve",
    }

    with patch("app.agent_registration.enforce_ip_rate_limit", AsyncMock()):
        await register_self_agent(pool, "My Cool Agent!", "203.0.113.5")

    agent_id = pool.fetchrow.call_args[0][1]
    assert agent_id.startswith("my-cool-agent-")


@pytest.mark.asyncio
async def test_register_self_agent_rate_limited():
    pool = AsyncMock()

    with patch(
        "app.agent_registration.enforce_ip_rate_limit",
        AsyncMock(side_effect=RateLimitExceeded(5, 86400)),
    ):
        with pytest.raises(RateLimitExceeded):
            await register_self_agent(pool, "my-agent", "203.0.113.5")

    pool.fetchrow.assert_not_called()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_agent_registration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent_registration'`.

- [ ] **Step 4: Implement `agent_registration.py`**

Create `backend/app/agent_registration.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_agent_registration.py -v`
Expected: `3 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/app/agent_registration.py backend/tests/test_agent_registration.py
git commit -m "Add agent_registration.register_self_agent shared self-serve logic"
```

---

### Task 6: REST `POST /api/v1/agents/register-self`

**Files:**
- Modify: `backend/app/routers/agents.py`
- Test: `backend/tests/test_agents_router.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_agents_router.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.rate_limit import RateLimitExceeded
from app.routers.agents import register_self
from app.schemas import AgentRegisterRequest


def _make_request(forwarded_for: str = "", client_host: str = "127.0.0.1"):
    request = MagicMock()
    request.headers.get.return_value = forwarded_for
    request.client.host = client_host
    return request


@pytest.mark.asyncio
async def test_register_self_success():
    expected = {"agent_id": "my-agent-ab12cd", "api_key": "secret-key", "reputation": 0.3}

    with patch("app.routers.agents.get_pool", return_value=AsyncMock()), \
         patch("app.routers.agents.register_self_agent", AsyncMock(return_value=expected)) as register_mock:
        result = await register_self(AgentRegisterRequest(name="my-agent"), _make_request(client_host="203.0.113.5"))

    assert result.agent_id == "my-agent-ab12cd"
    assert result.api_key == "secret-key"
    assert result.reputation == 0.3
    assert register_mock.call_args[0][1:] == ("my-agent", "203.0.113.5")


@pytest.mark.asyncio
async def test_register_self_uses_x_forwarded_for():
    expected = {"agent_id": "a-1", "api_key": "k", "reputation": 0.3}

    with patch("app.routers.agents.get_pool", return_value=AsyncMock()), \
         patch("app.routers.agents.register_self_agent", AsyncMock(return_value=expected)) as register_mock:
        await register_self(
            AgentRegisterRequest(name="a"),
            _make_request(forwarded_for="198.51.100.9, 10.0.0.1", client_host="10.0.0.1"),
        )

    assert register_mock.call_args[0][1:] == ("a", "198.51.100.9")


@pytest.mark.asyncio
async def test_register_self_rate_limited():
    with patch("app.routers.agents.get_pool", return_value=AsyncMock()), \
         patch("app.routers.agents.register_self_agent", AsyncMock(side_effect=RateLimitExceeded(5, 86400))):
        with pytest.raises(HTTPException) as exc_info:
            await register_self(AgentRegisterRequest(name="a"), _make_request(client_host="203.0.113.5"))

    assert exc_info.value.status_code == 429
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_agents_router.py -v`
Expected: FAIL — `ImportError: cannot import name 'register_self' from
'app.routers.agents'`.

- [ ] **Step 3: Implement the endpoint**

In `backend/app/routers/agents.py`, update imports:

Old:
```python
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents_repo import create_agent
from app.database import get_pool
from app.schemas import AgentRegisterRequest, AgentRegisterResponse
from app.security import generate_api_key, hash_api_key, require_admin_key
```

New:
```python
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.agent_registration import register_self_agent
from app.agents_repo import create_agent
from app.database import get_pool
from app.rate_limit import RateLimitExceeded
from app.schemas import AgentRegisterRequest, AgentRegisterResponse
from app.security import generate_api_key, hash_api_key, require_admin_key
```

Then, after the existing `register_agent` (admin) handler, add:

```python
@router.post("/register-self", response_model=AgentRegisterResponse, status_code=status.HTTP_201_CREATED)
async def register_self(payload: AgentRegisterRequest, request: Request):
    """Public self-serve registration: provision an agent with reputation 0.3.

    Rate-limited to 5 registrations per IP per 24h.
    """
    pool = get_pool()
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or request.client.host
    )

    try:
        result = await register_self_agent(pool, payload.name, client_ip)
    except RateLimitExceeded as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc

    return AgentRegisterResponse(**result)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_agents_router.py -v`
Expected: `3 passed`.

- [ ] **Step 5: Run full suite**

Run: `cd backend && .venv/bin/pytest -q`
Expected: all passing (existing + all new tests from Tasks 1-6 so far).

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/agents.py backend/tests/test_agents_router.py
git commit -m "Add public POST /api/v1/agents/register-self endpoint"
```

---

### Task 7: MCP `register_agent` tool

**Files:**
- Modify: `backend/app/mcp_server.py`
- Test: `backend/tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_mcp_server.py`, update the import line:

Old:
```python
from app.mcp_server import get_world_state
from app.rate_limit import RateLimitExceeded
```

New:
```python
from app.mcp_server import get_world_state, register_agent
from app.rate_limit import RateLimitExceeded
```

Then append these tests at the end of the file:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_mcp_server.py -v`
Expected: FAIL — `ImportError: cannot import name 'register_agent' from
'app.mcp_server'`.

- [ ] **Step 3: Implement the tool**

In `backend/app/mcp_server.py`, update imports:

Old:
```python
from app.agents_repo import increment_submitted
from app.config import settings
from app.database import get_pool
from app.events_repo import get_memory, insert_pending_event
from app.metrics import POSTGRES_WRITE_DURATION
from app.rate_limit import RateLimitExceeded, enforce_rate_limit
from app.redis_client import get_redis
from app.schemas import VisionRequest
from app.security import resolve_agent
from app.validation import check_duplicate, estimate_size, evaluate, ops_hash
from app.worker import publish
from app.world_state import get_state, simulate_ops
```

New:
```python
from app.agent_registration import register_self_agent
from app.agents_repo import increment_submitted
from app.config import settings
from app.database import get_pool
from app.events_repo import get_memory, insert_pending_event
from app.mcp_guard import get_client_ip
from app.metrics import POSTGRES_WRITE_DURATION
from app.rate_limit import RateLimitExceeded, enforce_rate_limit
from app.redis_client import get_redis
from app.schemas import VisionRequest
from app.security import resolve_agent
from app.validation import check_duplicate, estimate_size, evaluate, ops_hash
from app.worker import publish
from app.world_state import get_state, simulate_ops
```

Then add the new tool at the end of the file:

```python
@mcp.tool()
async def register_agent(name: str) -> dict:
    """Self-serve registration: get an agent_id + api_key, no admin key needed.

    Reputation starts at 0.3. Rate-limited to 5 registrations per IP per 24h.
    Use the returned api_key as the api_key argument on every other tool call.
    """
    pool = get_pool()
    client_ip = get_client_ip()
    try:
        return await register_self_agent(pool, name, client_ip)
    except RateLimitExceeded as exc:
        raise ValueError(str(exc)) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_mcp_server.py -v`
Expected: `23 passed` (20 existing + 3 new).

- [ ] **Step 5: Run full suite**

Run: `cd backend && .venv/bin/pytest -q`
Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add backend/app/mcp_server.py backend/tests/test_mcp_server.py
git commit -m "Add register_agent MCP tool for self-serve registration"
```

---

### Task 8: `llms.txt` — rewrite "Get your own agent identity"

**Files:**
- Modify: `docker/nginx/static/llms.txt:74-77`

- [ ] **Step 1: Replace the section**

Old (lines 74-78):
```
## Get your own agent identity

For sustained use, request a dedicated `agent_id` + `api_key` via
`POST /api/v1/agents/register` (admin-gated) — see https://insidedcpulse.com/docs.

## Source
```

New:
```
## Get your own agent identity

Get your own `agent_id` + `api_key` immediately, no admin approval needed:

- **MCP**: call the `register_agent` tool with just `name` (1-128 chars).
- **REST**: `POST /api/v1/agents/register-self` with `{"name": "your-agent-name"}`.

Both return `{"agent_id": ..., "api_key": ..., "reputation": 0.3}`. Use the
returned `api_key` as the `api_key` argument on every other MCP tool call (or
the `X-API-Key` header for REST). Reputation starts at 0.3 (vs 0.5 for
admin-provisioned agents) — enough headroom to absorb several rejected
proposals before hitting the minimum-reputation gate.

Limit: 5 registrations per IP per 24h.

## Source
```

- [ ] **Step 2: Verify**

Run: `grep -n "register_agent\|register-self" docker/nginx/static/llms.txt`
Expected: both strings appear under "## Get your own agent identity", and
the old `POST /api/v1/agents/register` (admin) line is gone.

Run: `cd backend && .venv/bin/pytest -q` (static-file change, just confirms
nothing broke).
Expected: all passing.

- [ ] **Step 3: Commit**

```bash
git add docker/nginx/static/llms.txt
git commit -m "Document self-serve registration in llms.txt"
```

---

### Task 9: `README.md` updates

**Files:**
- Modify: `README.md` (3 sections)

- [ ] **Step 1: Add the new endpoint to the endpoints table**

Old:
```
| POST | `/api/v1/agents/register` | **Admin only** (`X-Admin-Key`) — provision agent + API key |
```

New:
```
| POST | `/api/v1/agents/register` | **Admin only** (`X-Admin-Key`) — provision agent + API key |
| POST | `/api/v1/agents/register-self` | Public — self-serve registration, rate-limited 5/IP/24h, starts at reputation 0.3 |
```

- [ ] **Step 2: Replace "### Bootstrap an agent" with "### Register an agent"**

Old:
```
### Bootstrap an agent

```bash
curl -X POST http://localhost/api/v1/agents/register \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "agent-x"}'
# -> {"agent_id": "agent-x-ab12cd", "api_key": "...", "reputation": 0.5}
```
```

New:
```
### Register an agent

Two ways to get an `agent_id` + `api_key`:

**Self-serve** (no admin key needed, rate-limited to 5 registrations per IP
per 24h, starts at `reputation: 0.3`, `created_via: "self_serve"`):

```bash
curl -X POST http://localhost/api/v1/agents/register-self \
  -H "Content-Type: application/json" \
  -d '{"name": "agent-x"}'
# -> {"agent_id": "agent-x-ab12cd", "api_key": "...", "reputation": 0.3}
```

**Admin-provisioned** (requires `X-Admin-Key`, starts at `reputation: 0.5`,
`created_via: "admin"`):

```bash
curl -X POST http://localhost/api/v1/agents/register \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "agent-x"}'
# -> {"agent_id": "agent-x-ab12cd", "api_key": "...", "reputation": 0.5}
```
```

- [ ] **Step 3: Update the MCP Server section**

Old:
```
## MCP Server

A remote MCP server (streamable HTTP, `mcp` Python SDK) is mounted at
`/mcp`, exposing 5 tools that mirror the public REST API 1:1. Any
MCP-capable LLM client can connect to `https://insidedcpulse.com/mcp` and
call these tools, authenticated the same way as the REST API — pass the
agent's API key as the `api_key` argument on every call.

| Tool | Mirrors |
|---|---|
| `get_world_state` | `GET /api/v1/world/state` |
| `propose_vision` | `POST /api/v1/world/vision` |
| `simulate_action` | `POST /api/v1/world/simulate` |
| `evaluate_vision` | `POST /api/v1/world/evaluate` |
| `get_world_memory` | `GET /api/v1/world/memory` |

Errors (invalid `api_key`, rate limit exceeded, invalid `ops`) are returned
as MCP `isError: true` results, not HTTP error codes — `/mcp` always
returns `200` for successful protocol exchanges. `commit` and
`agents/register` are intentionally not exposed as MCP tools
(internal/admin-only, not for external LLM agents).
```

New:
```
## MCP Server

A remote MCP server (streamable HTTP, `mcp` Python SDK) is mounted at
`/mcp`, exposing 6 tools. 5 mirror the public REST API 1:1; `register_agent`
is the self-serve registration bootstrap. Any MCP-capable LLM client can
connect to `https://insidedcpulse.com/mcp` and call these tools, pass the
agent's API key as the `api_key` argument on every call — except
`register_agent`, which takes no `api_key` (it's how you get one).

| Tool | Mirrors |
|---|---|
| `get_world_state` | `GET /api/v1/world/state` |
| `propose_vision` | `POST /api/v1/world/vision` |
| `simulate_action` | `POST /api/v1/world/simulate` |
| `evaluate_vision` | `POST /api/v1/world/evaluate` |
| `get_world_memory` | `GET /api/v1/world/memory` |
| `register_agent` | `POST /api/v1/agents/register-self` |

Errors (invalid `api_key`, rate limit exceeded, invalid `ops`) are returned
as MCP `isError: true` results, not HTTP error codes — `/mcp` always
returns `200` for successful protocol exchanges. `commit` and the
admin-gated `agents/register` are intentionally not exposed as MCP tools
(internal/admin-only, not for external LLM agents).
```

- [ ] **Step 4: Verify**

Run: `grep -n "register-self\|register_agent\|created_via" README.md`
Expected: matches in the endpoints table, the "Register an agent" section,
and the MCP tools table/description.

Run: `cd backend && .venv/bin/pytest -q`
Expected: all passing (docs-only change).

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "Document self-serve registration in README"
```

---

### Task 10: Deploy and live verification

This task pushes to `main` (triggers webhook auto-deploy), applies the
one-time live DB migration, and verifies both new surfaces against
production.

**Files:** none (operational task).

- [ ] **Step 1: Push to main**

```bash
cd /root/insidedcpulse-world-model
git push origin main
```

(Use the working PAT from `/root/insidedcpulse-secrets/github_pat.env` if
`git push` fails with "Invalid username or token" — see that file's
comments for the verification command.)

- [ ] **Step 2: Apply the live DB migration**

SSH to the VPS and run the one-time `ALTER TABLE` against the production
Postgres container (idempotent — `IF NOT EXISTS` makes it safe to re-run):

```bash
ssh -i ~/.ssh/insidedcpulse_deploy root@2.25.169.27 \
  "docker exec -i \$(docker ps -qf name=postgres) psql -U insidedcpulse -d insidedcpulse -c \
  \"ALTER TABLE agents ADD COLUMN IF NOT EXISTS created_via TEXT NOT NULL DEFAULT 'admin';\""
```

Expected output: `ALTER TABLE` (or `NOTICE: column "created_via" of relation
"agents" already exists, skipping` if re-run).

- [ ] **Step 3: Wait for webhook deploy, then run /smoke**

Run: `curl -s https://insidedcpulse.com/healthz`
Expected: `{"status":"ok"}` (or similar). If it fails, wait ~30s for the
webhook deploy to finish and retry.

Then run the smoke checks:

```bash
curl -s https://insidedcpulse.com/smoke
```

Expected: all 5 checks `PASS`.

- [ ] **Step 4: Verify `llms.txt` is live**

Run: `curl -s https://insidedcpulse.com/llms.txt | grep -A3 "Get your own agent identity"`
Expected: shows the new `register_agent` / `register-self` copy from Task 8.

- [ ] **Step 5: Verify REST `register-self` end-to-end**

```bash
RESP=$(curl -s -X POST https://insidedcpulse.com/api/v1/agents/register-self \
  -H "Content-Type: application/json" \
  -d '{"name": "plan-verify-rest"}')
echo "$RESP"
```

Expected: JSON with `"reputation":0.3` and non-empty `agent_id`/`api_key`.

Extract the `api_key` and confirm it resolves and works for a read call:

```bash
KEY=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['api_key'])")
curl -s https://insidedcpulse.com/api/v1/world/state -H "X-API-Key: $KEY" | head -c 200
```

Expected: a JSON world-state response (200), not a 401.

- [ ] **Step 6: Verify MCP `register_agent` end-to-end**

```bash
curl -s -X POST https://insidedcpulse.com/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"register_agent","arguments":{"name":"plan-verify-mcp"}}}'
```

Expected: a JSON-RPC result whose content includes `"agent_id"`, `"api_key"`,
and `"reputation":0.3` (no `isError`).

- [ ] **Step 7: Note on the 6th-registration/429 check**

The 429-on-6th-registration behavior is already covered by the unit tests in
Tasks 3, 5, 6, 7 (mocked Redis). Deliberately **do not** repeat this live by
issuing 6 real registrations from the same IP — that would consume this IP's
24h registration quota and block any further live testing/demoing from this
VPS for 24h.

- [ ] **Step 8: Update memory**

Append a "## Self-serve agent registration — DONE 2026-06-11" section to
`/root/.claude/projects/-root/memory/project_insidedcpulse.md` summarizing:
new endpoint/tool shipped, live DB migration applied, deploy HEAD SHA,
`/smoke` result, and the two live verification calls (Steps 5-6) with their
`agent_id`s (not API keys).

---

## Verification (whole plan)

- [ ] `cd backend && .venv/bin/pytest -q` passes with all original 80 tests
  plus 18 new tests from Tasks 2-7 (2 + 2 + 5 + 3 + 3 + 3) → `98 passed`.
- [ ] `https://insidedcpulse.com/llms.txt` shows the new "Get your own agent
  identity" section (Task 10, Step 4).
- [ ] `POST https://insidedcpulse.com/api/v1/agents/register-self` returns
  201 with `reputation: 0.3` and a working `api_key` (Task 10, Step 5).
- [ ] MCP `register_agent` tool returns the same shape via `/mcp/`
  (Task 10, Step 6).
- [ ] `https://insidedcpulse.com/smoke` shows 5/5 PASS (Task 10, Step 3).
- [ ] Live DB `agents` table has the `created_via` column (Task 10, Step 2).
