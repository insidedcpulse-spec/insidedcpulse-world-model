import asyncpg

from app.config import settings


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


async def increment_submitted(pool: asyncpg.Pool, agent_id: str) -> None:
    await pool.execute("UPDATE agents SET total_submitted = total_submitted + 1 WHERE id = $1", agent_id)


async def apply_outcome(pool_or_conn, agent_id: str, accepted: bool) -> float:
    """Adjust reputation deterministically based on the validation outcome.

    Reputation is clamped to [0, 1]. Accepted events nudge it up,
    rejected events nudge it down — repeated spam drives an agent
    below `min_reputation_to_submit`, which then hard-fails future submissions.
    """
    delta = settings.reputation_step_accept if accepted else -settings.reputation_step_reject
    column = "total_accepted" if accepted else "total_rejected"
    row = await pool_or_conn.fetchrow(
        f"""
        UPDATE agents
        SET reputation = LEAST(1.0, GREATEST(0.0, reputation + $2)),
            {column} = {column} + 1
        WHERE id = $1
        RETURNING reputation
        """,
        agent_id, delta,
    )
    return float(row["reputation"])


async def get_all_reputations(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT id, reputation, total_submitted, total_accepted, total_rejected FROM agents WHERE id != 'system'"
    )
    return [dict(r) for r in rows]
