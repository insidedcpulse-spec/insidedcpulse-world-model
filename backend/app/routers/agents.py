import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.agent_registration import register_self_agent
from app.agents_repo import create_agent
from app.database import get_pool
from app.rate_limit import RateLimitExceeded
from app.schemas import AgentRegisterRequest, AgentRegisterResponse
from app.security import generate_api_key, hash_api_key, require_admin_key

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "agent"


@router.post("/register", response_model=AgentRegisterResponse, status_code=status.HTTP_201_CREATED)
async def register_agent(payload: AgentRegisterRequest, _: None = Depends(require_admin_key)):
    """Admin-only: provision a new agent identity and API key.

    The returned api_key is shown ONCE — only its sha256 hash is stored.
    """
    pool = get_pool()
    agent_id = f"{_slugify(payload.name)}-{secrets.token_hex(3)}"
    api_key = generate_api_key()

    try:
        agent = await create_agent(pool, agent_id, payload.name, hash_api_key(api_key))
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "could not register agent") from exc

    return AgentRegisterResponse(agent_id=agent["id"], api_key=api_key, reputation=float(agent["reputation"]))


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
