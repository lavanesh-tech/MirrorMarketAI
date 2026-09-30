"""Workspace-scoped retrieval over ingested evidence."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser, EmbedderDep, SessionDep
from app.schemas.search import SearchHitResponse, SearchRequest, SearchResponse
from app.services.search import SearchFilters, SearchService

router = APIRouter(tags=["search"])


@router.post(
    "/workspaces/{workspace_id}/search",
    response_model=SearchResponse,
    summary="Hybrid (full-text + vector, RRF) search over the workspace's sources",
)
async def search_workspace(
    workspace_id: uuid.UUID,
    body: SearchRequest,
    user: CurrentUser,
    session: SessionDep,
    embedder: EmbedderDep,
) -> SearchResponse:
    result = await SearchService(session, embedder).search(
        workspace_id,
        user,
        body.query,
        mode=body.mode,
        limit=body.limit,
        filters=SearchFilters(
            product_ids=body.product_ids,
            source_ids=body.source_ids,
            authorities=[a.value for a in body.authorities],
            source_types=[t.value for t in body.source_types],
        ),
    )
    return SearchResponse(
        query=body.query,
        mode=body.mode,
        embedding_model=result.model,
        degraded=result.degraded,
        items=[
            SearchHitResponse(
                chunk_id=hit.chunk.id,
                document_id=hit.chunk.document_id,
                source_id=hit.chunk.source_id,
                product_id=hit.chunk.product_id,
                workspace_id=hit.chunk.workspace_id,
                chunk_index=hit.chunk.chunk_index,
                char_start=hit.chunk.char_start,
                char_end=hit.chunk.char_end,
                text=hit.chunk.text,
                source_title=hit.source.title,
                source_url=hit.source.url,
                authority=hit.source.authority,
                source_type=hit.source.source_type,
                score=hit.score,
                lexical_rank=hit.lexical_rank,
                vector_rank=hit.vector_rank,
                similarity=hit.similarity,
            )
            for hit in result.hits
        ],
    )
