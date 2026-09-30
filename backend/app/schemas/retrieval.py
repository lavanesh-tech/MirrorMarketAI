"""Chunk and embedding-job contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.workspaces import PageMeta


class EmbeddingJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    model: str
    status: str
    attempts: int
    chunk_count: int
    embedded_count: int
    tokens_used: int
    last_error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class ChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chunk_index: int
    char_start: int
    char_end: int
    token_estimate: int
    text: str


class ChunkListResponse(BaseModel):
    document_id: uuid.UUID
    items: list[ChunkResponse]
    page: PageMeta
