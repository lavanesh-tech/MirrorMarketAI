"""Chunking + embedding jobs.

Job lifecycle:  PENDING --claim--> RUNNING --ok--> SUCCEEDED
                                      └--error--> PENDING (retry) | FAILED (attempts exhausted)

Work is idempotent: chunks are created once per document, and embeddings are
unique per (chunk, model), so a crashed or repeated job only fills in gaps.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.core.config import Settings
from app.models.retrieval import ChunkEmbedding, DocumentChunk, EmbeddingJob, JobStatus
from app.models.sources import SourceDocument
from app.providers.embeddings import EmbeddingProvider
from app.retrieval.chunking import chunk_text, estimate_tokens
from app.telemetry import metrics

logger = logging.getLogger(__name__)

_ACTIVE = (JobStatus.PENDING.value, JobStatus.RUNNING.value, JobStatus.SUCCEEDED.value)


def embedding_model_name(settings: Settings) -> str:
    return (
        settings.openai_embedding_model if settings.embedding_provider == "openai" else "hashing-v1"
    )


async def enqueue_embedding_job(
    session: AsyncSession, document_id: uuid.UUID, model: str
) -> EmbeddingJob:
    """Create a PENDING job unless an active/successful one already exists (no commit)."""
    existing = await session.scalar(
        select(EmbeddingJob)
        .where(
            EmbeddingJob.document_id == document_id,
            EmbeddingJob.model == model,
            EmbeddingJob.status.in_(_ACTIVE),
        )
        .order_by(EmbeddingJob.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        return existing
    job = EmbeddingJob(document_id=document_id, model=model, status=JobStatus.PENDING.value)
    session.add(job)
    await session.flush()
    return job


class EmbeddingService:
    def __init__(
        self, session: AsyncSession, settings: Settings, provider: EmbeddingProvider
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider = provider

    async def claim_next(self) -> EmbeddingJob | None:
        """Atomically take the oldest PENDING job; concurrent workers skip locked rows."""
        job = await self.session.scalar(
            select(EmbeddingJob)
            .where(
                EmbeddingJob.status == JobStatus.PENDING.value,
                EmbeddingJob.model == self.provider.model,
            )
            .order_by(EmbeddingJob.created_at, EmbeddingJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        self._mark_running(job)
        await self.session.commit()
        return job

    def _mark_running(self, job: EmbeddingJob) -> None:
        job.status = JobStatus.RUNNING.value
        job.attempts += 1
        job.started_at = datetime.now(UTC)
        job.last_error = None

    async def process_now(self, document_id: uuid.UUID) -> EmbeddingJob:
        """Synchronous path used by the API: enqueue (or reuse) and run immediately."""
        job = await enqueue_embedding_job(self.session, document_id, self.provider.model)
        if job.status == JobStatus.SUCCEEDED.value:
            return job
        self._mark_running(job)
        await self.session.commit()
        return await self.run(job)

    async def run(self, job: EmbeddingJob) -> EmbeddingJob:
        job_id = job.id
        try:
            chunks = await self._ensure_chunks(job.document_id)
            job.chunk_count = len(chunks)
            embedded, tokens = await self._embed_missing(chunks)
            job.embedded_count = embedded
            job.tokens_used += tokens
            job.status = JobStatus.SUCCEEDED.value
            metrics.EMBEDDING_JOBS.labels(result="succeeded").inc()
            job.finished_at = datetime.now(UTC)
            await self.session.commit()
            logger.info(
                "embedding job succeeded",
                extra={"job_id": str(job_id), "chunks": len(chunks), "tokens": tokens},
            )
        except Exception as exc:
            await self.session.rollback()
            failed = await self.session.get(EmbeddingJob, job_id)
            if failed is None:  # document deleted mid-run
                raise
            exhausted = failed.attempts >= self.settings.embedding_job_max_attempts
            failed.status = (JobStatus.FAILED if exhausted else JobStatus.PENDING).value
            metrics.EMBEDDING_JOBS.labels(result="failed" if exhausted else "retry").inc()
            failed.last_error = f"{type(exc).__name__}: {exc}"[:500]
            failed.finished_at = datetime.now(UTC)
            await self.session.commit()
            logger.warning(
                "embedding job failed",
                extra={"job_id": str(job_id), "attempts": failed.attempts, "final": exhausted},
            )
            return failed
        return job

    async def _ensure_chunks(self, document_id: uuid.UUID) -> list[DocumentChunk]:
        existing = list(
            await self.session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
        if existing:
            return existing
        document = await self.session.scalar(
            select(SourceDocument)
            .options(undefer(SourceDocument.text))
            .where(SourceDocument.id == document_id)
        )
        if document is None:
            raise LookupError("document no longer exists")
        pieces = chunk_text(
            document.text,
            target_chars=self.settings.chunk_target_chars,
            overlap_chars=self.settings.chunk_overlap_chars,
        )
        chunks = [
            DocumentChunk(
                document_id=document.id,
                source_id=document.source_id,
                product_id=document.product_id,
                workspace_id=document.workspace_id,
                chunk_index=piece.index,
                text=piece.text,
                char_start=piece.char_start,
                char_end=piece.char_end,
                token_estimate=estimate_tokens(piece.text),
                content_hash=piece.content_hash,
            )
            for piece in pieces
        ]
        self.session.add_all(chunks)
        await self.session.flush()
        return chunks

    async def _embed_missing(self, chunks: list[DocumentChunk]) -> tuple[int, int]:
        if not chunks:
            return 0, 0
        done = set(
            await self.session.scalars(
                select(ChunkEmbedding.chunk_id).where(
                    ChunkEmbedding.chunk_id.in_([c.id for c in chunks]),
                    ChunkEmbedding.model == self.provider.model,
                )
            )
        )
        todo = [c for c in chunks if c.id not in done]
        tokens = 0
        size = self.settings.embedding_batch_size
        for start in range(0, len(todo), size):
            batch = todo[start : start + size]
            result = await self.provider.embed([c.text for c in batch])
            tokens += result.total_tokens
            self.session.add_all(
                ChunkEmbedding(chunk_id=c.id, model=self.provider.model, embedding=vector)
                for c, vector in zip(batch, result.vectors, strict=True)
            )
            await self.session.flush()
        return len(done) + len(todo), tokens
