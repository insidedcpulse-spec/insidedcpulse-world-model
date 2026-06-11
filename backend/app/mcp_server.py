from mcp.server.fastmcp import FastMCP

import json
import uuid
from datetime import datetime, timezone

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

mcp = FastMCP("InsideDCPulse", stateless_http=True)

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
