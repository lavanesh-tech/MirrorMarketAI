"""Chunking/embedding endpoints for ingested sources."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, EmbedderDep, SessionDep, SettingsDep
from app.core.errors import EmbeddingJobNotFoundError, SourceNotFoundError
from app.models.catalog import Product
from app.models.retrieval import DocumentChunk, EmbeddingJob
from app.models.sources import SourceDocument
from app.repositories.base import MAX_PAGE_SIZE
from app.schemas.retrieval import ChunkListResponse, ChunkResponse, EmbeddingJobResponse
from app.schemas.workspaces import PageMeta
from app.services.embeddings import EmbeddingService
from app.services.sources import SourceService

router = APIRouter(tags=["embeddings"])


@router.post(
    "/sources/{source_id}/embed",
    response_model=EmbeddingJobResponse,
    summary="Chunk and embed the source's latest document now (idempotent)",
)
async def embed_source(
    source_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    embedder: EmbedderDep,
) -> EmbeddingJobResponse:
    sources = SourceService(session, settings)
    source = await sources.get_source(source_id, user)
    product = await session.get(Product, source.product_id)
    assert product is not None  # noqa: S101 - FK guarantees it
    await sources.require_write(product, source.workspace_id, user)
    document = await sources.latest_document(source_id, user)
    if document is None:
        raise SourceNotFoundError("This source has not been ingested yet.")
    job = await EmbeddingService(session, settings, embedder).process_now(document.id)
    return EmbeddingJobResponse.model_validate(job)


@router.get("/sources/{source_id}/chunks", response_model=ChunkListResponse)
async def list_chunks(
    source_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ChunkListResponse:
    document = await SourceService(session, settings).latest_document(source_id, user)
    if document is None:
        raise SourceNotFoundError("This source has not been ingested yet.")
    where = DocumentChunk.document_id == document.id
    total = await session.scalar(select(func.count()).select_from(DocumentChunk).where(where)) or 0
    rows = await session.scalars(
        select(DocumentChunk)
        .where(where)
        .order_by(DocumentChunk.chunk_index)
        .limit(limit)
        .offset(offset)
    )
    return ChunkListResponse(
        document_id=document.id,
        items=[ChunkResponse.model_validate(c) for c in rows],
        page=PageMeta(total=total, limit=limit, offset=offset),
    )


@router.get("/embedding-jobs/{job_id}", response_model=EmbeddingJobResponse)
async def get_embedding_job(
    job_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> EmbeddingJobResponse:
    job = await session.get(EmbeddingJob, job_id)
    if job is None:
        raise EmbeddingJobNotFoundError
    document = await session.get(SourceDocument, job.document_id)
    if document is None:
        raise EmbeddingJobNotFoundError
    try:  # same visibility as the source the job belongs to
        await SourceService(session, settings).get_source(document.source_id, user)
    except SourceNotFoundError as exc:
        raise EmbeddingJobNotFoundError from exc
    return EmbeddingJobResponse.model_validate(job)
