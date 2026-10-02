"""Evidence packs: freeze search results into citable items, then validate cited text."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import EvidencePackNotFoundError, NoEvidenceFoundError
from app.domain.citations import CitationReport, validate_citations
from app.domain.roles import WorkspaceRole
from app.models.evidence import EvidenceItem, EvidencePack
from app.models.identity import User
from app.models.requirements import PurchaseRequirement
from app.providers.embeddings import EmbeddingProvider
from app.services.search import SearchFilters, SearchHit, SearchMode, SearchService
from app.services.workspaces import WorkspaceService


class EvidenceService:
    def __init__(self, session: AsyncSession, embedder: EmbeddingProvider) -> None:
        self.session = session
        self.embedder = embedder
        self.workspaces = WorkspaceService(session)

    async def create(
        self,
        workspace_id: uuid.UUID,
        user: User,
        query: str,
        *,
        mode: SearchMode,
        limit: int,
        filters: SearchFilters,
    ) -> EvidencePack:
        await self.workspaces.authorize(workspace_id, user, WorkspaceRole.MEMBER)
        result = await SearchService(self.session, self.embedder).search(
            workspace_id, user, query, mode=mode, limit=limit, filters=filters
        )
        if not result.hits:
            raise NoEvidenceFoundError
        pack = await self.freeze(
            workspace_id,
            user,
            query,
            mode=mode,
            hits=result.hits,
            embedding_model=result.model,
            degraded=result.degraded,
        )
        # `freeze` only flushes, so agents can save a pack together with their run.
        await self.session.commit()
        return pack

    async def freeze(
        self,
        workspace_id: uuid.UUID,
        user: User,
        query: str,
        *,
        mode: str,
        hits: list[SearchHit],
        embedding_model: str | None,
        degraded: bool,
    ) -> EvidencePack:
        """Persist hits (already authorized and ranked) as a pack with markers E1..En."""
        requirement_version = await self.session.scalar(
            select(PurchaseRequirement.current_version).where(
                PurchaseRequirement.workspace_id == workspace_id,
                PurchaseRequirement.current_version > 0,
            )
        )
        pack = EvidencePack(
            workspace_id=workspace_id,
            created_by_id=user.id,
            query=query[:500],
            mode=mode,
            embedding_model=embedding_model,
            requirement_version=requirement_version,
            degraded=degraded,
            items=[
                EvidenceItem(
                    position=position,
                    chunk_id=hit.chunk.id,
                    source_id=hit.source.id,
                    product_id=hit.chunk.product_id,
                    text=hit.chunk.text,
                    char_start=hit.chunk.char_start,
                    char_end=hit.chunk.char_end,
                    content_hash=hit.chunk.content_hash,
                    source_title=hit.source.title,
                    source_url=hit.source.url,
                    authority=hit.source.authority,
                    source_type=hit.source.source_type,
                    score=hit.score,
                )
                for position, hit in enumerate(hits, start=1)
            ],
        )
        self.session.add(pack)
        await self.session.flush()
        return pack

    async def get(self, workspace_id: uuid.UUID, user: User, pack_id: uuid.UUID) -> EvidencePack:
        await self.workspaces.authorize(workspace_id, user)
        pack = await self.session.scalar(
            select(EvidencePack).where(
                EvidencePack.id == pack_id, EvidencePack.workspace_id == workspace_id
            )
        )
        if pack is None:
            raise EvidencePackNotFoundError
        return pack

    async def list(
        self, workspace_id: uuid.UUID, user: User, *, limit: int, offset: int
    ) -> tuple[list[EvidencePack], int]:
        await self.workspaces.authorize(workspace_id, user)
        where = EvidencePack.workspace_id == workspace_id
        total = await self.session.scalar(
            select(func.count()).select_from(EvidencePack).where(where)
        )
        rows = await self.session.scalars(
            select(EvidencePack)
            .where(where)
            .order_by(EvidencePack.created_at.desc(), EvidencePack.id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows), total or 0

    async def validate(
        self, workspace_id: uuid.UUID, user: User, pack_id: uuid.UUID, text: str
    ) -> CitationReport:
        pack = await self.get(workspace_id, user, pack_id)
        return validate_citations(text, {item.position: item.text for item in pack.items})
