"""Agent-run contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.workspaces import PageMeta

MAX_WEIGHTS = 60
MAX_WEIGHT = 10


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent: str
    product_id: uuid.UUID | None
    evidence_pack_id: uuid.UUID | None
    requirement_version: int | None
    status: str
    engine: str
    degraded: bool
    output: dict[str, Any] | None
    validation: dict[str, Any] | None
    error: str | None
    duration_ms: int
    tokens_used: int
    created_by_id: uuid.UUID
    created_at: datetime


class AgentRunListResponse(BaseModel):
    items: list[AgentRunResponse]
    page: PageMeta


class CompatibilityRequest(BaseModel):
    owned_devices: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Overrides the requirements' owned_devices when non-empty",
        examples=[["iPhone 15", "USB-C dock", "Sony TV"]],
    )


class AnalyzeRequest(BaseModel):
    product_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=50, description="Default: every workspace product"
    )


class CompareRequest(BaseModel):
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    weights: dict[str, float] = Field(
        default_factory=dict,
        description="Per-criterion weight overrides (0-10), e.g. {'ram_gb': 5, 'price': 1}",
    )
    budget_is_hard: bool = True

    @field_validator("weights")
    @classmethod
    def _weight_range(cls, value: dict[str, float]) -> dict[str, float]:
        if len(value) > MAX_WEIGHTS or any(not 0 <= w <= MAX_WEIGHT for w in value.values()):
            raise ValueError(f"at most {MAX_WEIGHTS} weights, each between 0 and {MAX_WEIGHT}")
        return value


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=8, ge=1, le=20)
