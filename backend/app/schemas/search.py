"""Hybrid search contracts."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.sources import SourceAuthority, SourceType

MAX_FILTER_VALUES = 50


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    mode: Literal["hybrid", "lexical", "vector"] = "hybrid"
    limit: int = Field(default=10, ge=1, le=50)
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    source_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    authorities: list[SourceAuthority] = Field(default_factory=list, max_length=3)
    source_types: list[SourceType] = Field(default_factory=list, max_length=len(SourceType))

    @field_validator("query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("query must not be blank")
        return value


class SearchHitResponse(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    source_id: uuid.UUID
    product_id: uuid.UUID
    workspace_id: uuid.UUID | None
    chunk_index: int
    char_start: int
    char_end: int
    text: str
    source_title: str
    source_url: str | None
    authority: str
    source_type: str
    score: float = Field(description="Reciprocal Rank Fusion score (higher is better)")
    lexical_rank: int | None
    vector_rank: int | None
    similarity: float | None = Field(description="Cosine similarity, when vector search ran")


class SearchResponse(BaseModel):
    query: str
    mode: str
    embedding_model: str | None
    degraded: bool = Field(description="True when hybrid search fell back to full-text only")
    items: list[SearchHitResponse]
