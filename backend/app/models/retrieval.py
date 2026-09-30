"""Chunks, embeddings and embedding jobs.

    SourceDocument 1──* DocumentChunk 1──* ChunkEmbedding (one per embedding model)
    SourceDocument 1──* EmbeddingJob

Chunks copy product_id / workspace_id / source_id from their document, so
retrieval (Phase 7) can filter by tenant and product with plain indexed WHERE
clauses before doing any vector math.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import EMBEDDING_DIMENSIONS
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class DocumentChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        CheckConstraint("char_end > char_start", name="span_valid"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_sources.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("comparison_workspaces.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    token_estimate: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))


class ChunkEmbedding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One vector per (chunk, model), so a model can be swapped without data loss."""

    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "model"),
        # Approximate nearest-neighbour index for cosine distance (<=>).
        Index(
            "ix_chunk_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(String(100))
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))


class EmbeddingJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable unit of work: 'chunk + embed this document with this model'.

    Workers claim PENDING jobs with SELECT ... FOR UPDATE SKIP LOCKED, so many
    workers can run in parallel without double-processing. Kafka (Phase 21)
    will trigger work; this table remains the source of truth for job state.
    """

    __tablename__ = "embedding_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s.value}'" for s in JobStatus) + ")",
            name="status_valid",
        ),
        Index("ix_embedding_jobs_pending", "created_at", postgresql_where="status = 'PENDING'"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    embedded_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
