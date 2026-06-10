# MCP Server (remote, streamable HTTP)

## Context

`README.md` documents an "MCP (future)" tool surface mapping to the
existing `/api/v1/world/*` HTTP API, never implemented. This design adds
a remote MCP server, mounted inside the existing FastAPI app at `/mcp`,
using the official `mcp` Python SDK's streamable HTTP transport. Any
MCP-capable LLM client can connect over `https://insidedcpulse.com/mcp`
and call 5 tools that mirror the public REST API, authenticated the same
way (per-agent API key).

## Architecture

```
LLM client (MCP)
  -> POST/GET https://insidedcpulse.com/mcp   (streamable HTTP)
  -> nginx location /mcp -> api:8000
  -> FastAPI app, mounted sub-app: mcp.streamable_http_app()
  -> backend/app/mcp_server.py: FastMCP("InsideDCPulse"), 5 @mcp.tool()s
       - each tool takes api_key: str (== X-API-Key header value)
       - resolve_agent(pool, api_key) -> agent dict | None
       - enforce_rate_limit(agent_id, limit)
       - reuses existing business logic (validation.py, world_state.py,
         events_repo.py, worker.publish) — same code path as the REST
         routers, no internal HTTP calls
```

Dependency: add `mcp==1.9.1` to `backend/requirements.txt`.

## Components

### 1. `backend/app/security.py` (refactor, no behavior change for HTTP)

Extract the lookup logic from `get_current_agent` into a reusable
function:

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

### 2. `backend/app/rate_limit.py` (refactor, no behavior change for HTTP)

Extract the increment/check logic into a reusable function and a
dedicated exception:

```python
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
    async def dependency(agent: dict = Depends(get_current_agent)) -> dict:
        try:
            await enforce_rate_limit(agent["id"], limit)
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
        return agent
    return dependency
```

### 3. `backend/app/mcp_server.py` (new)

```python
import json
import uuid
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

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

### 4. `backend/app/main.py` (modify)

```python
from app.mcp_server import mcp

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

# ... after app.include_router(world.router) / agents.router / ws.router
# and after the @app.get("/"), /healthz, /metrics routes are defined:
app.mount("/", mcp.streamable_http_app(), name="mcp")
```

The mount is added **last**, after every other route definition, so it
only matches paths not already handled (`/mcp`).

### 5. `docker/nginx/conf.d/insidedcpulse.conf.ssl` (modify)

Add before the catch-all `location /`:

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

### 6. `backend/requirements.txt` (modify)

Add `mcp==1.9.1`.

## Error handling

| Condition | Tool behavior |
|---|---|
| Invalid/unknown `api_key` | `ValueError("invalid API key")` -> MCP error result |
| Rate limit exceeded | `ValueError("rate limit exceeded (N/60s)")` -> MCP error result |
| Invalid `ops`/`description` (pydantic) | pydantic `ValidationError` (subclass of `ValueError`) -> MCP error result |
| `propose_vision` payload too large | `ValueError("payload too large (...)")` |
| `propose_vision` duplicate within 60s | `ValueError("duplicate event: ...")` |

No new HTTP status codes — MCP errors are returned inside the MCP
protocol response (`isError: true`), not as HTTP error codes (the HTTP
layer for `/mcp` always returns 200 for successful protocol exchanges).

## Testing

First backend test suite: `backend/tests/test_mcp_server.py` +
`backend/tests/test_security.py` + `backend/tests/test_rate_limit.py`,
using `pytest` + `pytest-asyncio` + `unittest.mock`. No real
Postgres/Redis — `get_pool()`/`get_redis()` and repo functions
(`resolve_agent`, `enforce_rate_limit`, `get_state`, `simulate_ops`,
`evaluate`, `get_memory`, `insert_pending_event`, `increment_submitted`,
`publish`, `check_duplicate`) are patched with `unittest.mock.AsyncMock`.

Coverage:
- `resolve_agent`: found / not found
- `enforce_rate_limit`: under limit / over limit (raises `RateLimitExceeded`)
- Each of the 5 tools: happy path (mocked deps return expected dict),
  invalid `api_key` -> `ValueError`, rate-limit exceeded -> `ValueError`
- `propose_vision`: payload-too-large -> `ValueError`, duplicate -> `ValueError`
- `evaluate_vision` / `simulate_action` / `get_world_memory`: pydantic
  validation error on malformed `ops` (e.g. unknown `op` value)

Add `pytest==8.3.4` and `pytest-asyncio==0.25.2` to
`backend/requirements-dev.txt` (new file, not installed in the
production image).

End-to-end (manual, after deploy via webhook):
1. `curl -i https://insidedcpulse.com/mcp` with an MCP-protocol
   `initialize` request (or use an MCP client / `mcp dev` inspector) ->
   confirm streamable HTTP handshake succeeds (not 404/502).
2. Call `get_world_state` with a valid agent API key -> matches
   `GET /api/v1/world/state` response shape.
3. Call any tool with an invalid `api_key` -> MCP error result
   `"invalid API key"`.

## Out of scope

- No OAuth / MCP auth spec (api_key as a tool argument only).
- No new MCP resources or prompts — tools only.
- `commit` and `agents/register` endpoints are not exposed as MCP tools
  (internal/admin-only, not for external LLM agents).
- Local stdio MCP variant — not built (remote-only, per design decision).
