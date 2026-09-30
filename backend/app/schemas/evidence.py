"""Evidence-pack and citation-validation contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.search import SearchRequest
from app.schemas.workspaces import PageMeta


class CreateEvidencePackRequest(SearchRequest):
    limit: int = Field(default=8, ge=1, le=20)


class EvidenceItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    marker: str
    chunk_id: uuid.UUID | None
    source_id: uuid.UUID | None
    product_id: uuid.UUID
    text: str
    char_start: int
    char_end: int
    content_hash: str
    source_title: str
    source_url: str | None
    authority: str
    source_type: str
    score: float


class EvidencePackSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    query: str
    mode: str
    embedding_model: str | None
    requirement_version: int | None
    degraded: bool
    created_by_id: uuid.UUID
    created_at: datetime


class EvidencePackResponse(EvidencePackSummary):
    items: list[EvidenceItemResponse]


class EvidencePackListResponse(BaseModel):
    items: list[EvidencePackSummary]
    page: PageMeta


class ValidateCitationsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
