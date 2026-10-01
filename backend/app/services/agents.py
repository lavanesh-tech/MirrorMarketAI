"""Runs agents synchronously and records every run (orchestration arrives in Phase 15)."""

from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentResult
from app.agents.product_research import (
    Candidate,
    build_output,
    extract_value,
    llm_values,
    query_for,
    research_targets,
)
from app.core.config import Settings
from app.core.errors import AgentRunNotFoundError, WorkspaceProductNotFoundError
from app.domain.citations import validate_citations
from app.domain.requirements import RequirementSpec
from app.domain.roles import WorkspaceRole
from app.models.agents import AgentRun
from app.models.catalog import Product, ProductSpecification, WorkspaceProduct
from app.models.identity import User
from app.models.requirements import PurchaseRequirement, RequirementVersion
from app.providers.embeddings import EmbeddingProvider
from app.providers.llm import LLMError, OpenAIChatClient
from app.services.evidence import EvidenceService
from app.services.search import SearchFilters, SearchHit, SearchService
from app.services.workspaces import WorkspaceService

logger = logging.getLogger(__name__)

PRODUCT_RESEARCH = "product_research"
RULES_ENGINE = "rules-v1"


class AgentService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        embedder: EmbeddingProvider,
        llm: OpenAIChatClient | None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.embedder = embedder
        self.llm = llm
        self.workspaces = WorkspaceService(session)

    async def _current_spec(
        self, workspace_id: uuid.UUID
    ) -> tuple[RequirementSpec | None, int | None]:
        row = await self.session.execute(
            select(RequirementVersion.spec, RequirementVersion.version)
            .join(PurchaseRequirement, PurchaseRequirement.id == RequirementVersion.requirement_id)
            .where(
                PurchaseRequirement.workspace_id == workspace_id,
                RequirementVersion.version == PurchaseRequirement.current_version,
            )
        )
        found = row.first()
        if found is None:
            return None, None
        return RequirementSpec.model_validate(found.spec), found.version

    async def research_product(
        self, workspace_id: uuid.UUID, user: User, product_id: uuid.UUID
    ) -> AgentRun:
        started = time.perf_counter()
        await self.workspaces.authorize(workspace_id, user, WorkspaceRole.MEMBER)
        linked = await self.session.scalar(
            select(WorkspaceProduct.id).where(
                WorkspaceProduct.workspace_id == workspace_id,
                WorkspaceProduct.product_id == product_id,
            )
        )
        product = await self.session.get(Product, product_id)
        if linked is None or product is None:
            raise WorkspaceProductNotFoundError

        spec, requirement_version = await self._current_spec(workspace_id)
        targets = research_targets(product, spec)

        search = SearchService(self.session, self.embedder)
        per_key: dict[str, list[SearchHit]] = {}
        ordered: dict[uuid.UUID, SearchHit] = {}
        embedding_model: str | None = None
        search_degraded = False
        for target in targets:
            result = await search.search(
                workspace_id,
                user,
                query_for(target.key),
                mode="hybrid",
                limit=self.settings.agent_evidence_per_criterion,
                filters=SearchFilters(product_ids=[product_id]),
            )
            per_key[target.key] = result.hits
            embedding_model = embedding_model or result.model
            search_degraded = search_degraded or result.degraded
            for hit in result.hits:
                ordered.setdefault(hit.chunk.id, hit)

        pack = None
        if ordered:
            pack = await EvidenceService(self.session, self.embedder).freeze(
                workspace_id,
                user,
                f"{PRODUCT_RESEARCH}: {product.brand} {product.name}",
                mode="hybrid",
                hits=list(ordered.values()),
                embedding_model=embedding_model,
                degraded=search_degraded,
            )
        position = {chunk_id: i for i, chunk_id in enumerate(ordered, start=1)}
        evidence = {i: hit.chunk.text for i, hit in enumerate(ordered.values(), start=1)}

        catalog = list(
            await self.session.scalars(
                select(ProductSpecification).where(ProductSpecification.product_id == product_id)
            )
        )
        engine, degraded, tokens = RULES_ENGINE, search_degraded, 0
        llm_summary: str | None = None
        values: dict[str, Candidate] | None = None
        if self.llm is not None and evidence:
            try:
                values, llm_summary, tokens = await llm_values(
                    self.llm, product, [t.key for t in targets], evidence
                )
                engine = f"openai:{self.llm.model}"
            except LLMError as exc:
                logger.warning("product research degraded to rules", extra={"error": str(exc)})
                degraded = True
        if values is None:
            values = {}
            for target in targets:
                for hit in per_key[target.key]:
                    extracted = extract_value(target.key, hit.chunk.text)
                    if extracted is not None:
                        number, text, unit = extracted
                        values[target.key] = Candidate(number, text, unit, position[hit.chunk.id])
                        break

        output = build_output(product, targets, values, catalog)
        if llm_summary:
            output = output.model_copy(update={"summary": llm_summary})
        agent_result = AgentResult(
            output=output,
            validation=validate_citations(output.summary, evidence),
            engine=engine,
            degraded=degraded,
            tokens_used=tokens,
        )
        run = AgentRun(
            workspace_id=workspace_id,
            agent=PRODUCT_RESEARCH,
            product_id=product_id,
            evidence_pack_id=pack.id if pack else None,
            requirement_version=requirement_version,
            status="SUCCEEDED",
            engine=agent_result.engine,
            degraded=agent_result.degraded,
            output=agent_result.output.model_dump(mode="json"),
            validation=agent_result.validation.model_dump(mode="json"),
            duration_ms=int((time.perf_counter() - started) * 1000),
            tokens_used=agent_result.tokens_used,
            created_by_id=user.id,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_run(self, workspace_id: uuid.UUID, user: User, run_id: uuid.UUID) -> AgentRun:
        await self.workspaces.authorize(workspace_id, user)
        run = await self.session.scalar(
            select(AgentRun).where(AgentRun.id == run_id, AgentRun.workspace_id == workspace_id)
        )
        if run is None:
            raise AgentRunNotFoundError
        return run

    async def list_runs(
        self,
        workspace_id: uuid.UUID,
        user: User,
        *,
        agent: str | None,
        product_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[list[AgentRun], int]:
        await self.workspaces.authorize(workspace_id, user)
        conditions = [AgentRun.workspace_id == workspace_id]
        if agent:
            conditions.append(AgentRun.agent == agent)
        if product_id:
            conditions.append(AgentRun.product_id == product_id)
        total = await self.session.scalar(
            select(func.count()).select_from(AgentRun).where(*conditions)
        )
        rows = await self.session.scalars(
            select(AgentRun)
            .where(*conditions)
            .order_by(AgentRun.created_at.desc(), AgentRun.id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows), total or 0
