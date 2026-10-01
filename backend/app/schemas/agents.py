"""Agent-run contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.workspaces import PageMeta


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
