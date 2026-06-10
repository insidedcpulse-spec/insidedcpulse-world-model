import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

OpType = Literal["set", "merge", "increment", "delete"]


class WorldOp(BaseModel):
    """A single deterministic mutation against the world state."""

    op: OpType
    key: str = Field(min_length=1, max_length=256)
    value: Any | None = None


class VisionRequest(BaseModel):
    event_type: Literal["vision", "action"] = "vision"
    description: str = Field(min_length=1, max_length=2000)
    ops: list[WorldOp] = Field(min_length=1, max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VisionAccepted(BaseModel):
    event_id: uuid.UUID
    status: Literal["queued"] = "queued"
    submitted_at: datetime


class EvaluateResponse(BaseModel):
    score: float
    would_accept: bool
    reasons: list[str]


class SimulateOpResult(BaseModel):
    key: str
    op: OpType
    before: Any | None
    after: Any | None


class SimulateResponse(BaseModel):
    valid: bool
    reasons: list[str]
    results: list[SimulateOpResult]
    drift: float


class CommitRequest(BaseModel):
    agent_id: str = "system"
    event_type: Literal["genesis", "internal"] = "internal"
    description: str = Field(min_length=1, max_length=2000)
    ops: list[WorldOp] = Field(min_length=1, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorldStateEntry(BaseModel):
    key: str
    value: Any
    version: int
    updated_at: datetime


class WorldStateResponse(BaseModel):
    state: dict[str, WorldStateEntry]
    as_of: datetime


class MemoryEntry(BaseModel):
    id: int
    event_id: uuid.UUID
    agent_id: str
    event_type: str
    status: str
    score: float | None
    reason: str | None
    created_at: datetime
    processed_at: datetime | None
    payload: dict[str, Any]


class MemoryResponse(BaseModel):
    items: list[MemoryEntry]
    total: int
    limit: int
    offset: int


class AgentRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class AgentRegisterResponse(BaseModel):
    agent_id: str
    api_key: str
    reputation: float


class CommitResponse(BaseModel):
    event_id: uuid.UUID
    status: Literal["accepted"] = "accepted"
    world_state_diff: dict[str, Any]
