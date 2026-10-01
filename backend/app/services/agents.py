"""Runs agents synchronously and records every run (orchestration arrives in Phase 15)."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentResult
from app.agents.compatibility import Finding, detect, llm_findings, required_capabilities
from app.agents.compatibility import build_output as build_compatibility
from app.agents.compatibility import search_query as compat_query
from app.agents.product_research import (
    Candidate,
    build_output,
    extract_value,
    llm_values,
    query_for,
    research_targets,
)
from app.agents.review_intelligence import aggregate, llm_mentions, rule_mentions
from app.agents.risk import QUERIES as RISK_QUERIES
from app.agents.risk import agent_risks
from app.agents.risk import build_output as build_risk
from app.agents.risk import evidence_hits as risk_evidence_hits
from app.agents.synthesis import synthesize
from app.agents.value import PRICE_QUERY, Price, catalog_price, extract_price
from app.agents.value import build_output as build_value
from app.core.config import Settings
from app.core.errors import (
    AgentRunNotFoundError,
    NoCompatibilityTargetsError,
    WorkspaceProductNotFoundError,
)
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
REVIEW_INTELLIGENCE = "review_intelligence"
COMPATIBILITY = "compatibility"
VALUE = "value"
RISK = "risk"
SYNTHESIS = "synthesis"
ORCHESTRATION = "orchestration"
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

    async def current_spec(
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
        product = await self._workspace_product(workspace_id, user, product_id)

        spec, requirement_version = await self.current_spec(workspace_id)
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
        return await self.record(
            workspace_id,
            user,
            PRODUCT_RESEARCH,
            product_id,
            pack.id if pack else None,
            requirement_version,
            AgentResult(
                output=output,
                validation=validate_citations(output.summary, evidence),
                engine=engine,
                degraded=degraded,
                tokens_used=tokens,
            ),
            started,
        )

    async def analyze_reviews(
        self, workspace_id: uuid.UUID, user: User, product_id: uuid.UUID
    ) -> AgentRun:
        started = time.perf_counter()
        product = await self._workspace_product(workspace_id, user, product_id)
        _, requirement_version = await self.current_spec(workspace_id)
        hits = await SearchService(self.session, self.embedder).scoped_chunks(
            workspace_id,
            user,
            SearchFilters(product_ids=[product_id], source_types=["REVIEW"]),
            limit=self.settings.agent_max_review_chunks,
        )
        pack = None
        if hits:
            pack = await EvidenceService(self.session, self.embedder).freeze(
                workspace_id,
                user,
                f"{REVIEW_INTELLIGENCE}: {product.brand} {product.name}",
                mode="scope",
                hits=hits,
                embedding_model=None,
                degraded=False,
            )
        evidence = {i: hit.chunk.text for i, hit in enumerate(hits, start=1)}

        engine, degraded, tokens = RULES_ENGINE, False, 0
        mentions = None
        if self.llm is not None and evidence:
            try:
                mentions, tokens = await llm_mentions(self.llm, product, evidence)
                engine = f"openai:{self.llm.model}"
            except LLMError as exc:
                logger.warning("review analysis degraded to rules", extra={"error": str(exc)})
                degraded = True
        if mentions is None:
            mentions = rule_mentions(evidence)

        output = aggregate(product, mentions, review_chunks=len(hits))
        return await self.record(
            workspace_id,
            user,
            REVIEW_INTELLIGENCE,
            product_id,
            pack.id if pack else None,
            requirement_version,
            AgentResult(
                output=output,
                validation=validate_citations(output.summary, evidence),
                engine=engine,
                degraded=degraded,
                tokens_used=tokens,
            ),
            started,
        )

    async def check_compatibility(
        self,
        workspace_id: uuid.UUID,
        user: User,
        product_id: uuid.UUID,
        owned_devices: list[str] | None,
    ) -> AgentRun:
        started = time.perf_counter()
        product = await self._workspace_product(workspace_id, user, product_id)
        spec, requirement_version = await self.current_spec(workspace_id)
        owned = owned_devices if owned_devices else (spec.owned_devices if spec else [])
        if not owned:
            raise NoCompatibilityTargetsError
        requirements = required_capabilities(owned, product.category)

        search = SearchService(self.session, self.embedder)
        per_cap: dict[str, list[SearchHit]] = {}
        ordered: dict[uuid.UUID, SearchHit] = {}
        embedding_model: str | None = None
        search_degraded = False
        for req in requirements:
            result = await search.search(
                workspace_id,
                user,
                compat_query(req.capability),
                mode="hybrid",
                limit=self.settings.agent_evidence_per_criterion,
                filters=SearchFilters(product_ids=[product_id]),
            )
            per_cap[req.capability] = result.hits
            embedding_model = embedding_model or result.model
            search_degraded = search_degraded or result.degraded
            for hit in result.hits:
                ordered.setdefault(hit.chunk.id, hit)

        pack = None
        if ordered:
            pack = await EvidenceService(self.session, self.embedder).freeze(
                workspace_id,
                user,
                f"{COMPATIBILITY}: {product.brand} {product.name}",
                mode="hybrid",
                hits=list(ordered.values()),
                embedding_model=embedding_model,
                degraded=search_degraded,
            )
        position = {chunk_id: i for i, chunk_id in enumerate(ordered, start=1)}
        evidence = {i: hit.chunk.text for i, hit in enumerate(ordered.values(), start=1)}

        engine, degraded, tokens = RULES_ENGINE, search_degraded, 0
        findings: dict[str, Finding] | None = None
        if self.llm is not None and evidence:
            try:
                findings, tokens = await llm_findings(
                    self.llm, product, [r.capability for r in requirements], evidence
                )
                engine = f"openai:{self.llm.model}"
            except LLMError as exc:
                logger.warning("compatibility degraded to rules", extra={"error": str(exc)})
                degraded = True
        if findings is None:
            findings = {}
            for req in requirements:
                for hit in per_cap[req.capability]:
                    detected = detect(req.capability, hit.chunk.text)
                    if detected is not None:
                        support, quote = detected
                        findings[req.capability] = Finding(support, position[hit.chunk.id], quote)
                        break

        catalog = list(
            await self.session.scalars(
                select(ProductSpecification).where(ProductSpecification.product_id == product_id)
            )
        )
        output = build_compatibility(product, owned, requirements, findings, catalog)
        return await self.record(
            workspace_id,
            user,
            COMPATIBILITY,
            product_id,
            pack.id if pack else None,
            requirement_version,
            AgentResult(
                output=output,
                validation=validate_citations(output.summary, evidence),
                engine=engine,
                degraded=degraded,
                tokens_used=tokens,
            ),
            started,
        )

    async def assess_value(
        self, workspace_id: uuid.UUID, user: User, product_id: uuid.UUID
    ) -> AgentRun:
        started = time.perf_counter()
        product = await self._workspace_product(workspace_id, user, product_id)
        spec, requirement_version = await self.current_spec(workspace_id)
        result = await SearchService(self.session, self.embedder).search(
            workspace_id,
            user,
            PRICE_QUERY,
            mode="hybrid",
            limit=self.settings.agent_evidence_per_criterion,
            filters=SearchFilters(product_ids=[product_id]),
        )
        price: Price | None = None
        pack = None
        evidence: dict[int, str] = {}
        if result.hits:
            pack = await EvidenceService(self.session, self.embedder).freeze(
                workspace_id,
                user,
                f"{VALUE}: {product.brand} {product.name}",
                mode="hybrid",
                hits=result.hits,
                embedding_model=result.model,
                degraded=result.degraded,
            )
            evidence = {i: hit.chunk.text for i, hit in enumerate(result.hits, start=1)}
            for position, text in evidence.items():
                if (found := extract_price(text)) is not None:
                    amount, currency, quote = found
                    price = Price(
                        amount=amount,
                        currency=currency,
                        source="evidence",
                        citations=[f"E{position}"],
                        quote=quote,
                    )
                    break
        if price is None:
            catalog = await self.session.scalars(
                select(ProductSpecification).where(ProductSpecification.product_id == product_id)
            )
            price = catalog_price(list(catalog))

        research = await self._latest_run(
            workspace_id, product_id, PRODUCT_RESEARCH, requirement_version
        )
        output = build_value(
            product,
            price,
            spec,
            research.output if research else None,
            str(research.id) if research else None,
        )
        return await self.record(
            workspace_id,
            user,
            VALUE,
            product_id,
            pack.id if pack else None,
            requirement_version,
            AgentResult(
                output=output,
                validation=validate_citations(output.summary, evidence),
                engine=RULES_ENGINE,
                degraded=result.degraded,
            ),
            started,
        )

    async def assess_risk(
        self, workspace_id: uuid.UUID, user: User, product_id: uuid.UUID
    ) -> AgentRun:
        started = time.perf_counter()
        product = await self._workspace_product(workspace_id, user, product_id)
        spec, requirement_version = await self.current_spec(workspace_id)
        search = SearchService(self.session, self.embedder)
        ordered: dict[uuid.UUID, SearchHit] = {}
        embedding_model: str | None = None
        degraded = False
        for query in RISK_QUERIES.values():
            result = await search.search(
                workspace_id,
                user,
                query,
                mode="hybrid",
                limit=self.settings.agent_evidence_per_criterion,
                filters=SearchFilters(product_ids=[product_id]),
            )
            embedding_model = embedding_model or result.model
            degraded = degraded or result.degraded
            for hit in result.hits:
                ordered.setdefault(hit.chunk.id, hit)
        pack = None
        if ordered:
            pack = await EvidenceService(self.session, self.embedder).freeze(
                workspace_id,
                user,
                f"{RISK}: {product.brand} {product.name}",
                mode="hybrid",
                hits=list(ordered.values()),
                embedding_model=embedding_model,
                degraded=degraded,
            )
        evidence = {i: hit.chunk.text for i, hit in enumerate(ordered.values(), start=1)}

        runs = {
            agent: await self._latest_run(workspace_id, product_id, agent, requirement_version)
            for agent in (PRODUCT_RESEARCH, COMPATIBILITY, VALUE)
        }
        others = agent_risks(
            spec,
            *(run.output if run else None for run in runs.values()),
        )
        output = build_risk(product, risk_evidence_hits(evidence), others)
        return await self.record(
            workspace_id,
            user,
            RISK,
            product_id,
            pack.id if pack else None,
            requirement_version,
            AgentResult(
                output=output,
                validation=validate_citations(output.summary, evidence),
                engine=RULES_ENGINE,
                degraded=degraded,
            ),
            started,
        )

    async def synthesize_product(
        self, workspace_id: uuid.UUID, user: User, product_id: uuid.UUID
    ) -> AgentRun:
        """Combine the latest runs (same requirement version) into one verdict. No re-runs."""
        started = time.perf_counter()
        product = await self._workspace_product(workspace_id, user, product_id)
        spec, requirement_version = await self.current_spec(workspace_id)
        outputs: dict[str, dict[str, Any]] = {}
        run_ids: dict[str, str] = {}
        for agent in (PRODUCT_RESEARCH, REVIEW_INTELLIGENCE, COMPATIBILITY, VALUE, RISK):
            run = await self._latest_run(workspace_id, product_id, agent, requirement_version)
            if run is not None and run.output is not None:
                outputs[agent] = run.output
                run_ids[agent] = str(run.id)
        result = synthesize(product, spec, outputs, run_ids)
        return await self.record(
            workspace_id,
            user,
            SYNTHESIS,
            product_id,
            None,
            requirement_version,
            AgentResult(
                output=result,
                # The synthesis cites agent runs, not evidence items: nothing to validate.
                validation=validate_citations("", {}),
                engine=RULES_ENGINE,
            ),
            started,
        )

    async def record_failure(
        self,
        workspace_id: uuid.UUID,
        user: User,
        agent: str,
        product_id: uuid.UUID | None,
        error: str,
        duration_ms: int,
    ) -> AgentRun:
        _, requirement_version = await self.current_spec(workspace_id)
        run = AgentRun(
            workspace_id=workspace_id,
            agent=agent,
            product_id=product_id,
            requirement_version=requirement_version,
            status="FAILED",
            engine=RULES_ENGINE,
            error=error[:500],
            duration_ms=duration_ms,
            created_by_id=user.id,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    # ---------------------------------------------------------------- helpers
    async def _latest_run(
        self,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID,
        agent: str,
        requirement_version: int | None,
    ) -> AgentRun | None:
        """Most recent successful run of `agent` against the same requirement version."""
        run: AgentRun | None = await self.session.scalar(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.product_id == product_id,
                AgentRun.agent == agent,
                AgentRun.status == "SUCCEEDED",
                AgentRun.requirement_version.is_not_distinct_from(requirement_version),
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id)
            .limit(1)
        )
        return run

    async def _workspace_product(
        self, workspace_id: uuid.UUID, user: User, product_id: uuid.UUID
    ) -> Product:
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
        return product

    async def record(
        self,
        workspace_id: uuid.UUID,
        user: User,
        agent: str,
        product_id: uuid.UUID | None,
        evidence_pack_id: uuid.UUID | None,
        requirement_version: int | None,
        result: AgentResult,
        started: float,
    ) -> AgentRun:
        run = AgentRun(
            workspace_id=workspace_id,
            agent=agent,
            product_id=product_id,
            evidence_pack_id=evidence_pack_id,
            requirement_version=requirement_version,
            status="SUCCEEDED",
            engine=result.engine,
            degraded=result.degraded,
            output=result.output.model_dump(mode="json"),
            validation=result.validation.model_dump(mode="json"),
            duration_ms=int((time.perf_counter() - started) * 1000),
            tokens_used=result.tokens_used,
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
