"""Prometheus metrics. Grafana reads this — it is NOT a memory store.

All gauges/counters here back the 5 dashboards:
World Stability Index, AI Consensus Health, System Drift Meter,
Agent Reputation Map, Event Flow Timeline.
"""

import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# --- API layer ---
HTTP_REQUESTS_TOTAL = Counter(
    "insidedcpulse_http_requests_total", "HTTP requests", ["method", "endpoint", "status_code"]
)
HTTP_REQUEST_DURATION = Histogram(
    "insidedcpulse_http_request_duration_seconds", "HTTP request latency", ["endpoint"]
)
AGENT_REQUESTS_TOTAL = Counter(
    "insidedcpulse_agent_requests_total", "Requests per agent", ["agent_id"]
)

# --- Agent reputation ---
AGENT_REPUTATION = Gauge("insidedcpulse_agent_reputation", "Agent reputation score", ["agent_id"])
AGENT_REJECTION_RATE = Gauge("insidedcpulse_agent_rejection_rate", "Agent rejection rate", ["agent_id"])

# --- World model / event flow ---
WORLD_EVENTS_TOTAL = Counter(
    "insidedcpulse_world_events_total", "Processed events by outcome", ["status"]
)
WORLD_QUEUE_SIZE = Gauge("insidedcpulse_world_queue_size", "Pending events in Redis queue")
WORLD_STATE_KEYS = Gauge("insidedcpulse_world_state_keys", "Number of keys in world_state")

# --- Performance ---
POSTGRES_WRITE_DURATION = Histogram(
    "insidedcpulse_postgres_write_duration_seconds", "Postgres write latency"
)

# --- Consensus / drift ---
WORLD_CONSENSUS_SCORE = Gauge(
    "insidedcpulse_world_consensus_score", "EMA of validation scores across all agents"
)
WORLD_DRIFT = Gauge(
    "insidedcpulse_world_drift", "EMA of |simulated - committed| world state drift"
)

_EMA_ALPHA = 0.1
_consensus_ema = 0.5
_drift_ema = 0.0


def record_decision_score(score: float) -> None:
    global _consensus_ema
    _consensus_ema = _EMA_ALPHA * score + (1 - _EMA_ALPHA) * _consensus_ema
    WORLD_CONSENSUS_SCORE.set(_consensus_ema)


def record_drift(drift: float) -> None:
    global _drift_ema
    _drift_ema = _EMA_ALPHA * drift + (1 - _EMA_ALPHA) * _drift_ema
    WORLD_DRIFT.set(_drift_ema)


def record_world_event(status: str) -> None:
    WORLD_EVENTS_TOTAL.labels(status=status).inc()


def set_agent_reputation(agent_id: str, reputation: float, total_submitted: int, total_rejected: int) -> None:
    AGENT_REPUTATION.labels(agent_id=agent_id).set(reputation)
    rate = (total_rejected / total_submitted) if total_submitted else 0.0
    AGENT_REJECTION_RATE.labels(agent_id=agent_id).set(rate)


def set_queue_size(size: int) -> None:
    WORLD_QUEUE_SIZE.set(size)


def set_world_state_keys(count: int) -> None:
    WORLD_STATE_KEYS.set(count)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        route = request.scope.get("route")
        endpoint = route.path if route else request.url.path

        HTTP_REQUESTS_TOTAL.labels(
            method=request.method, endpoint=endpoint, status_code=response.status_code
        ).inc()
        HTTP_REQUEST_DURATION.labels(endpoint=endpoint).observe(duration)
        return response


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
