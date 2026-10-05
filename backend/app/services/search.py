"""Workspace-scoped hybrid retrieval: Postgres full-text + pgvector, fused with RRF.

Visibility (enforced in SQL, before ranking):
- the caller must be a member of the workspace (else 404);
- a chunk is visible if it belongs to a source private to this workspace, or to
  a shared source (workspace_id NULL) of a product added to this workspace;
- only chunks of each source's latest document are searched, so stale
  versions of a page never surface.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import ColumnElement, and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import SearchUnavailableError
from app.models.catalog import WorkspaceProduct
from app.models.identity import User
from app.models.retrieval import ChunkEmbedding, DocumentChunk
from app.models.sources import ProductSource, SourceDocument, SourceSnapshot
from app.providers.embeddings import EmbeddingError, EmbeddingProvider
from app.retrieval.fusion import DEFAULT_RRF_K, reciprocal_rank_fusion
from app.services.workspaces import WorkspaceService
from app.telemetry import metrics

SearchMode = Literal["hybrid", "lexical", "vector"]

MIN_CANDIDATES = 20
CANDIDATE_MULTIPLIER = 4
TEXT_SEARCH_CONFIG = "english"


@dataclass(frozen=True, slots=True)
class SearchFilters:
    product_ids: list[uuid.UUID] = field(default_factory=list)
    source_ids: list[uuid.UUID] = field(default_factory=list)
    authorities: list[str] = field(default_factory=list)
    source_types: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk: DocumentChunk
    source: ProductSource
    score: float
    lexical_rank: int | None
    vector_rank: int | None
    similarity: float | None


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: list[SearchHit]
    model: str | None
    degraded: bool


class SearchService:
    def __init__(self, session: AsyncSession, embedder: EmbeddingProvider) -> None:
        self.session = session
        self.embedder = embedder

    async def search(
        self,
        workspace_id: uuid.UUID,
        user: User,
        query: str,
        *,
        mode: SearchMode = "hybrid",
        limit: int = 10,
        filters: SearchFilters | None = None,
    ) -> SearchResult:
        await WorkspaceService(self.session).authorize(workspace_id, user)
        with metrics.SEARCH_DURATION.labels(mode=mode).time():
            result = await self._search(workspace_id, query, mode, limit, filters)
        if result.degraded:
            metrics.SEARCH_DEGRADED.inc()
        return result

    async def _search(
        self,
        workspace_id: uuid.UUID,
        query: str,
        mode: SearchMode,
        limit: int,
        filters: SearchFilters | None,
    ) -> SearchResult:
        scope = self._scope(workspace_id, filters or SearchFilters())
        candidates = max(MIN_CANDIDATES, limit * CANDIDATE_MULTIPLIER)

        lexical: list[uuid.UUID] = []
        vector: list[tuple[uuid.UUID, float]] = []
        degraded = False
        model: str | None = None

        if mode in ("hybrid", "lexical"):
            lexical = await self._lexical(query, scope, candidates)
        if mode in ("hybrid", "vector"):
            try:
                vector = await self._vector(query, scope, candidates)
                model = self.embedder.model
            except EmbeddingError as exc:
                if mode == "vector":
                    raise SearchUnavailableError from exc
                degraded = True  # hybrid falls back to lexical-only results

        vector_ids = [chunk_id for chunk_id, _ in vector]
        fused = reciprocal_rank_fusion([lexical, vector_ids], k=DEFAULT_RRF_K)[:limit]
        if not fused:
            return SearchResult(hits=[], model=model, degraded=degraded)

        lexical_rank = {cid: i for i, cid in enumerate(lexical, start=1)}
        vector_rank = {cid: i for i, (cid, _) in enumerate(vector, start=1)}
        similarity = {cid: 1.0 - distance for cid, distance in vector}

        rows = await self.session.execute(
            select(DocumentChunk, ProductSource)
            .join(ProductSource, ProductSource.id == DocumentChunk.source_id)
            .where(DocumentChunk.id.in_([cid for cid, _ in fused]))
        )
        by_id = {chunk.id: (chunk, source) for chunk, source in rows}
        hits = [
            SearchHit(
                chunk=by_id[cid][0],
                source=by_id[cid][1],
                score=score,
                lexical_rank=lexical_rank.get(cid),
                vector_rank=vector_rank.get(cid),
                similarity=similarity.get(cid),
            )
            for cid, score in fused
            if cid in by_id
        ]
        return SearchResult(hits=hits, model=model, degraded=degraded)

    async def scoped_chunks(
        self, workspace_id: uuid.UUID, user: User, filters: SearchFilters, *, limit: int
    ) -> list[SearchHit]:
        """All visible chunks matching filters (no ranking), in document order."""
        await WorkspaceService(self.session).authorize(workspace_id, user)
        rows = await self.session.execute(
            select(DocumentChunk, ProductSource)
            .join(ProductSource, ProductSource.id == DocumentChunk.source_id)
            .where(*self._scope(workspace_id, filters))
            .order_by(ProductSource.created_at, DocumentChunk.source_id, DocumentChunk.chunk_index)
            .limit(limit)
        )
        return [
            SearchHit(chunk, source, 0.0, lexical_rank=None, vector_rank=None, similarity=None)
            for chunk, source in rows
        ]

    # ------------------------------------------------------------------ scope
    @staticmethod
    def _scope(workspace_id: uuid.UUID, filters: SearchFilters) -> list[ColumnElement[bool]]:
        workspace_products = select(WorkspaceProduct.product_id).where(
            WorkspaceProduct.workspace_id == workspace_id
        )
        latest_documents = (
            select(SourceDocument.id)
            .join(SourceSnapshot, SourceSnapshot.id == SourceDocument.snapshot_id)
            .ext(distinct_on(SourceDocument.source_id))
            .order_by(
                SourceDocument.source_id,
                SourceSnapshot.last_seen_at.desc(),
                SourceSnapshot.id.desc(),
            )
        )
        conditions: list[ColumnElement[bool]] = [
            or_(
                DocumentChunk.workspace_id == workspace_id,
                and_(
                    DocumentChunk.workspace_id.is_(None),
                    DocumentChunk.product_id.in_(workspace_products),
                ),
            ),
            DocumentChunk.document_id.in_(latest_documents),
        ]
        if filters.product_ids:
            conditions.append(DocumentChunk.product_id.in_(filters.product_ids))
        if filters.source_ids:
            conditions.append(DocumentChunk.source_id.in_(filters.source_ids))
        if filters.authorities or filters.source_types:
            source_filter = select(ProductSource.id)
            if filters.authorities:
                source_filter = source_filter.where(
                    ProductSource.authority.in_(filters.authorities)
                )
            if filters.source_types:
                source_filter = source_filter.where(
                    ProductSource.source_type.in_(filters.source_types)
                )
            conditions.append(DocumentChunk.source_id.in_(source_filter))
        return conditions

    # ---------------------------------------------------------------- rankers
    async def _lexical(
        self, query: str, scope: list[ColumnElement[bool]], candidates: int
    ) -> list[uuid.UUID]:
        # websearch_to_tsquery never raises on user input ("quotes", -negation, OR).
        tsquery = func.websearch_to_tsquery(TEXT_SEARCH_CONFIG, query)
        rank = func.ts_rank_cd(DocumentChunk.search_vector, tsquery)
        rows = await self.session.scalars(
            select(DocumentChunk.id)
            .where(*scope, DocumentChunk.search_vector.op("@@")(tsquery))
            .order_by(rank.desc(), DocumentChunk.id)
            .limit(candidates)
        )
        return list(rows)

    async def _vector(
        self, query: str, scope: list[ColumnElement[bool]], candidates: int
    ) -> list[tuple[uuid.UUID, float]]:
        batch = await self.embedder.embed([query])
        query_vector = batch.vectors[0]
        # With WHERE filters an HNSW scan can stop before finding enough matching
        # rows; pgvector >= 0.8 iterative scans keep searching until LIMIT is met.
        await self.session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
        await self.session.execute(text(f"SET LOCAL hnsw.ef_search = {max(40, int(candidates))}"))
        distance = ChunkEmbedding.embedding.cosine_distance(query_vector)
        rows = await self.session.execute(
            select(DocumentChunk.id, distance)
            .join(ChunkEmbedding, ChunkEmbedding.chunk_id == DocumentChunk.id)
            .where(*scope, ChunkEmbedding.model == self.embedder.model)
            .order_by(distance, DocumentChunk.id)
            .limit(candidates)
        )
        return [(chunk_id, float(dist)) for chunk_id, dist in rows]
