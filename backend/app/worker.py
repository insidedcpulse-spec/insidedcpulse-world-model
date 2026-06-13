"""Background queue consumer.

LLM
 -> POST /api/v1/world/vision   (router: auth, rate limit, dedup, enqueue)
 -> Redis queue (untrusted events)
 -> THIS WORKER: re-validate (deterministic), accept/reject
 -> PostgreSQL event store (append-only)
 -> world_state rebuild (only on accept)
 -> publish to /ws/world-stream
"""

import asyncio
import json
import logging
import uuid

import asyncpg
import redis.asyncio as redis

from app.agents_repo import apply_outcome, get_all_reputations
from app.config import settings
from app.events_repo import insert_committed_event, mark_processed
from app.metrics import (
    record_decision_score,
    record_drift,
    record_world_event,
    set_agent_reputation,
    set_queue_size,
    set_world_state_keys,
)
from app.projections import graph_projection
from app.schemas import VisionRequest
from app.validation import evaluate, ops_hash
from app.world_state import commit_ops

logger = logging.getLogger("insidedcpulse.worker")


def _drift_for(predicted, actual) -> float | None:
    if predicted is None:
        return None
    if isinstance(predicted, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(actual) - float(predicted))
    return 0.0 if predicted == actual else 1.0


async def publish(r: redis.Redis, message: dict) -> None:
    await r.publish(settings.stream_channel, json.dumps(message, default=str))


async def refresh_gauges(pool: asyncpg.Pool, r: redis.Redis) -> None:
    queue_size = await r.llen(settings.queue_key)
    set_queue_size(queue_size)

    state_keys = await pool.fetchval("SELECT count(*) FROM world_state")
    set_world_state_keys(state_keys)

    for agent in await get_all_reputations(pool):
        set_agent_reputation(
            agent["id"], float(agent["reputation"]), agent["total_submitted"], agent["total_rejected"]
        )


async def process_event(pool: asyncpg.Pool, r: redis.Redis, data: dict) -> None:
    event_id = uuid.UUID(data["event_id"])
    agent_id = data["agent_id"]
    payload = VisionRequest(**data["payload"])

    agent = await pool.fetchrow(
        "SELECT id, reputation, total_submitted, total_accepted, total_rejected FROM agents WHERE id = $1",
        agent_id,
    )
    if agent is None:
        await mark_processed(pool, event_id, "rejected", 0.0, "agent no longer exists")
        record_world_event("rejected")
        return
    agent = dict(agent)

    score, accept, reasons = await evaluate(pool, agent, payload)
    reason_text = "; ".join(reasons)
    record_decision_score(score)

    if accept:
        async with pool.acquire() as conn:
            async with conn.transaction():
                db_id = await mark_processed(conn, event_id, "accepted", score, reason_text)
                applied = await commit_ops(conn, payload.ops, db_id)
                await graph_projection.project_event(conn, db_id, agent_id, payload, applied)
                new_reputation = await apply_outcome(conn, agent_id, True)

        record_world_event("accepted")

        # Drift: compare against any prior /simulate prediction for the same ops.
        sim_key = f"sim:{agent_id}:{ops_hash(payload.ops)}"
        predicted_raw = await r.get(sim_key)
        predicted = json.loads(predicted_raw) if predicted_raw else {}
        for key, change in applied.items():
            drift = _drift_for(predicted.get(key), change["after"])
            if drift is not None:
                record_drift(drift)
                await pool.execute(
                    """
                    INSERT INTO drift_samples (key, simulated_value, committed_value, drift)
                    VALUES ($1, $2::jsonb, $3::jsonb, $4)
                    """,
                    key, json.dumps(predicted.get(key)), json.dumps(change["after"]), drift,
                )

        await publish(r, {
            "type": "event_accepted",
            "event_id": str(event_id),
            "agent_id": agent_id,
            "score": score,
            "reason": reason_text,
            "world_state_diff": applied,
        })
    else:
        await mark_processed(pool, event_id, "rejected", score, reason_text)
        new_reputation = await apply_outcome(pool, agent_id, False)
        record_world_event("rejected")
        await publish(r, {
            "type": "event_rejected",
            "event_id": str(event_id),
            "agent_id": agent_id,
            "score": score,
            "reason": reason_text,
        })

    logger.info("event %s by %s -> %s (score=%.4f, reputation=%.4f)",
                 event_id, agent_id, "accepted" if accept else "rejected", score, new_reputation)


async def worker_loop(pool: asyncpg.Pool, r: redis.Redis, stop_event: asyncio.Event) -> None:
    logger.info("worker started, polling %s", settings.queue_key)
    while not stop_event.is_set():
        try:
            item = await r.blpop(settings.queue_key, timeout=settings.worker_poll_timeout_seconds)
            if item is not None:
                _, raw = item
                await process_event(pool, r, json.loads(raw))
            await refresh_gauges(pool, r)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker iteration failed")
            await asyncio.sleep(1)
