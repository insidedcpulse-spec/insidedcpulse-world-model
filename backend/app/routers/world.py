import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.agents_repo import increment_submitted
from app.config import settings
from app.database import get_pool
from app.events_repo import get_memory, insert_committed_event, insert_pending_event
from app.metrics import POSTGRES_WRITE_DURATION, record_decision_score, record_world_event
from app.rate_limit import rate_limited
from app.redis_client import get_redis
from app.schemas import (
    CommitRequest,
    CommitResponse,
    EvaluateResponse,
    MemoryResponse,
    SimulateResponse,
    VisionAccepted,
    VisionRequest,
    WorldStateResponse,
)
from app.security import require_internal_key
from app.validation import check_duplicate, estimate_size, evaluate, ops_hash
from app.worker import publish
from app.world_state import commit_ops, get_state, simulate_ops

router = APIRouter(prefix="/api/v1/world", tags=["world"])

READ = settings.rate_limit_read_per_window
WRITE = settings.rate_limit_vision_per_window


@router.get("/state", response_model=WorldStateResponse)
async def world_state(agent: dict = Depends(rate_limited(READ))):
    """Current materialized world state — derived purely by replaying accepted events."""
    return await get_state(get_pool())


@router.post("/vision", response_model=VisionAccepted, status_code=status.HTTP_202_ACCEPTED)
async def submit_vision(payload: VisionRequest, agent: dict = Depends(rate_limited(WRITE))):
    """Propose a vision/action. Never applied directly — queued for deterministic validation."""
    pool = get_pool()
    r = get_redis()

    size = estimate_size(payload)
    if size > settings.max_payload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                             f"payload too large ({size} > {settings.max_payload_bytes} bytes)")

    if await check_duplicate(r, agent["id"], payload):
        raise HTTPException(status.HTTP_409_CONFLICT,
                             "duplicate event: identical submission within the last 60s")

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

    return VisionAccepted(event_id=event_id, submitted_at=datetime.now(timezone.utc))


@router.post("/simulate", response_model=SimulateResponse)
async def simulate(payload: VisionRequest, agent: dict = Depends(rate_limited(READ))):
    """Dry-run: apply ops against a copy of world_state, nothing is persisted."""
    pool = get_pool()
    r = get_redis()

    results, valid, reasons = await simulate_ops(pool, payload.ops)

    predicted = {res.key: res.after for res in results}
    sim_key = f"sim:{agent['id']}:{ops_hash(payload.ops)}"
    await r.set(sim_key, json.dumps(predicted, default=str), ex=300)

    return SimulateResponse(valid=valid, reasons=reasons, results=results, drift=0.0)


@router.post("/evaluate", response_model=EvaluateResponse)
async def evaluate_vision(payload: VisionRequest, agent: dict = Depends(rate_limited(READ))):
    """Score a vision against deterministic rules without queueing it."""
    pool = get_pool()
    score, would_accept, reasons = await evaluate(pool, agent, payload)
    return EvaluateResponse(score=score, would_accept=would_accept, reasons=reasons)


@router.post("/commit", response_model=CommitResponse)
async def commit(payload: CommitRequest, _: None = Depends(require_internal_key)):
    """Internal-only: directly append a system/genesis event and apply it to world_state."""
    pool = get_pool()
    r = get_redis()
    event_id = uuid.uuid4()

    with POSTGRES_WRITE_DURATION.time():
        async with pool.acquire() as conn:
            async with conn.transaction():
                db_id = await insert_committed_event(
                    conn, event_id, payload.agent_id, payload.event_type,
                    payload.model_dump(mode="json"), 1.0, "internal commit", "internal",
                )
                applied = await commit_ops(conn, payload.ops, db_id)

    record_world_event("accepted")
    record_decision_score(1.0)

    await publish(r, {
        "type": "event_accepted",
        "event_id": str(event_id),
        "agent_id": payload.agent_id,
        "score": 1.0,
        "reason": "internal commit",
        "world_state_diff": applied,
    })

    return CommitResponse(event_id=event_id, world_state_diff=applied)


@router.get("/memory", response_model=MemoryResponse)
async def memory(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    agent_id: str | None = None,
    event_status: str | None = Query(default=None, alias="status"),
    agent: dict = Depends(rate_limited(READ)),
):
    """Collective memory: paginated, filterable event log (the audit trail)."""
    pool = get_pool()
    return await get_memory(pool, limit, offset, agent_id, event_status)
