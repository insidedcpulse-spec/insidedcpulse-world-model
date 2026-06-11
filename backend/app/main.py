import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import close_pool, get_pool, init_pool
from app.mcp_guard import MCPMethodGuardMiddleware
from app.mcp_server import mcp
from app.metrics import MetricsMiddleware, metrics_response
from app.redis_client import close_redis, get_redis
from app.routers import agents, world, ws
from app.worker import worker_loop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("insidedcpulse")


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


app = FastAPI(
    title="InsideDCPulse",
    description="Event-Sourced World Model for Multi-LLM Agents",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(MetricsMiddleware)

app.include_router(world.router)
app.include_router(agents.router)
app.include_router(ws.router)


@app.get("/healthz", tags=["meta"])
async def healthz():
    pool = get_pool()
    await pool.fetchval("SELECT 1")
    await get_redis().ping()
    return {"status": "ok"}


@app.get("/metrics", tags=["meta"])
async def metrics():
    return metrics_response()


# Mounted last so it only matches paths not already handled by the routes above.
app.mount("/", MCPMethodGuardMiddleware(mcp.streamable_http_app()), name="mcp")
