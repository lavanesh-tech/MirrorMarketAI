"""Bounded orchestration: run every agent for the workspace's products, then synthesize.

Order per product (dependencies first): product_research -> review_intelligence ->
compatibility (only with owned devices) -> value -> risk -> synthesis.

Bounds (never interrupting a query mid-flight):
- time budget: once spent, remaining steps are SKIPPED (still synthesized from what exists);
- token budget: once spent, remaining steps run with the offline rules engines;
- product cap: at most ORCHESTRATION_MAX_PRODUCTS products per call.
A failing step is isolated in a savepoint, recorded as a FAILED run, and the pipeline
continues with the next step.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentResult
from app.agents.synthesis import ProductSynthesis, rank
from app.core.config import Settings
from app.core.errors import WorkspaceProductNotFoundError
from app.domain.citations import validate_citations
from app.domain.roles import WorkspaceRole
from app.models.agents import AgentRun
from app.models.catalog import WorkspaceProduct
from app.models.identity import User
from app.providers.embeddings import EmbeddingProvider
from app.providers.llm import OpenAIChatClient
from app.services.agents import (
    COMPATIBILITY,
    ORCHESTRATION,
    PRODUCT_RESEARCH,
    REVIEW_INTELLIGENCE,
    RISK,
    RULES_ENGINE,
    SYNTHESIS,
    VALUE,
    AgentService,
)
from app.services.workspaces import WorkspaceService

logger = logging.getLogger(__name__)

PIPELINE = (PRODUCT_RESEARCH, REVIEW_INTELLIGENCE, COMPATIBILITY, VALUE, RISK)


class StepStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class StepResult(BaseModel):
    agent: str
    status: StepStatus
    run_id: str | None = None
    duration_ms: int = 0
    engine: str | None = None
    reason: str | None = None


class ProductReport(BaseModel):
    product_id: str
    steps: list[StepResult]
    synthesis: ProductSynthesis | None


class OrchestrationOutput(BaseModel):
    ranking: list[ProductSynthesis]
    recommended_product_id: str | None
    products: list[ProductReport]
    products_skipped: list[str]
    time_budget_exhausted: bool
    token_budget_exhausted: bool
    tokens_used: int
    elapsed_ms: int


class Orchestrator:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        embedder: EmbeddingProvider,
        llm: OpenAIChatClient | None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session = session
        self.settings = settings
        self.agents = AgentService(session, settings, embedder, llm)
        self.agents.commit_runs = False  # each step is committed in `_run_step`
        self.clock = clock

    async def _products(
        self, workspace_id: uuid.UUID, product_ids: list[uuid.UUID] | None
    ) -> list[uuid.UUID]:
        linked = list(
            await self.session.scalars(
                select(WorkspaceProduct.product_id)
                .where(WorkspaceProduct.workspace_id == workspace_id)
                .order_by(WorkspaceProduct.created_at, WorkspaceProduct.id)
            )
        )
        if not product_ids:
            return linked
        missing = set(product_ids) - set(linked)
        if missing:
            raise WorkspaceProductNotFoundError
        return list(dict.fromkeys(product_ids))

    def _step(self, agent: str) -> Callable[[uuid.UUID, User, uuid.UUID], Awaitable[AgentRun]]:
        return {
            PRODUCT_RESEARCH: self.agents.research_product,
            REVIEW_INTELLIGENCE: self.agents.analyze_reviews,
            COMPATIBILITY: lambda ws, user, pid: self.agents.check_compatibility(
                ws, user, pid, None
            ),
            VALUE: self.agents.assess_value,
            RISK: self.agents.assess_risk,
        }[agent]

    async def analyze(
        self, workspace_id: uuid.UUID, user: User, product_ids: list[uuid.UUID] | None
    ) -> AgentRun:
        started = self.clock()
        perf_started = time.perf_counter()
        await WorkspaceService(self.session).authorize(workspace_id, user, WorkspaceRole.MEMBER)
        selected = await self._products(workspace_id, product_ids)
        cap = self.settings.orchestration_max_products
        to_run, skipped = selected[:cap], selected[cap:]
        spec, _ = await self.agents.current_spec(workspace_id)
        has_devices = bool(spec and spec.owned_devices)

        deadline = started + self.settings.orchestration_time_budget_seconds
        tokens = 0
        time_exhausted = token_exhausted = False
        reports: list[ProductReport] = []
        syntheses: list[ProductSynthesis] = []
        for product_id in to_run:
            steps: list[StepResult] = []
            for agent in PIPELINE:
                if self.clock() >= deadline:
                    time_exhausted = True
                    steps.append(
                        StepResult(
                            agent=agent, status=StepStatus.SKIPPED, reason="time budget exhausted"
                        )
                    )
                    continue
                if agent == COMPATIBILITY and not has_devices:
                    steps.append(
                        StepResult(
                            agent=agent, status=StepStatus.SKIPPED, reason="no owned devices"
                        )
                    )
                    continue
                if (
                    tokens >= self.settings.orchestration_token_budget
                    and self.agents.llm is not None
                ):
                    token_exhausted = True
                    self.agents.llm = None  # remaining steps use the offline engines
                step, used = await self._run_step(agent, workspace_id, user, product_id)
                steps.append(step)
                tokens += used
            synthesis_run = await self.agents.synthesize_product(workspace_id, user, product_id)
            synthesis = ProductSynthesis.model_validate(synthesis_run.output)
            steps.append(
                StepResult(
                    agent=SYNTHESIS,
                    status=StepStatus.SUCCEEDED,
                    run_id=str(synthesis_run.id),
                    duration_ms=synthesis_run.duration_ms,
                    engine=synthesis_run.engine,
                )
            )
            syntheses.append(synthesis)
            reports.append(
                ProductReport(product_id=str(product_id), steps=steps, synthesis=synthesis)
            )

        ranking = rank(syntheses)
        best = next((s.product_id for s in ranking if s.verdict == "RECOMMENDED"), None)
        output = OrchestrationOutput(
            ranking=ranking,
            recommended_product_id=best,
            products=reports,
            products_skipped=[str(p) for p in skipped],
            time_budget_exhausted=time_exhausted,
            token_budget_exhausted=token_exhausted,
            tokens_used=tokens,
            elapsed_ms=int((time.perf_counter() - perf_started) * 1000),
        )
        _, requirement_version = await self.agents.current_spec(workspace_id)
        run = await self.agents.record(
            workspace_id,
            user,
            ORCHESTRATION,
            None,
            None,
            requirement_version,
            AgentResult(output=output, validation=validate_citations("", {}), engine=RULES_ENGINE),
            perf_started,
        )
        await self.session.commit()
        return run

    async def _run_step(
        self, agent: str, workspace_id: uuid.UUID, user: User, product_id: uuid.UUID
    ) -> tuple[StepResult, int]:
        step_started = time.perf_counter()
        try:
            async with self.session.begin_nested():
                run = await self._step(agent)(workspace_id, user, product_id)
        except Exception as exc:
            logger.exception("agent step failed", extra={"agent": agent})
            elapsed = int((time.perf_counter() - step_started) * 1000)
            failed = await self.agents.record_failure(
                workspace_id, user, agent, product_id, type(exc).__name__, elapsed
            )
            await self.session.commit()
            return StepResult(
                agent=agent,
                status=StepStatus.FAILED,
                run_id=str(failed.id),
                duration_ms=elapsed,
                reason=type(exc).__name__,
            ), 0
        # Finished steps survive even if a later one, or the request itself, fails.
        await self.session.commit()
        return StepResult(
            agent=agent,
            status=StepStatus.SUCCEEDED,
            run_id=str(run.id),
            duration_ms=run.duration_ms,
            engine=run.engine,
        ), run.tokens_used
