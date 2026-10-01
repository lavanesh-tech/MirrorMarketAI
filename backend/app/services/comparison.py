"""Builds the comparison matrix from stored agent runs and scores it."""

from __future__ import annotations

import time
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentResult
from app.core.config import Settings
from app.core.errors import NothingToCompareError, WorkspaceProductNotFoundError
from app.domain.citations import validate_citations
from app.domain.comparison import (
    PRICE_KEY,
    Candidate,
    Cell,
    CellStatus,
    ComparisonResult,
    columns_for,
    compare,
)
from app.domain.roles import WorkspaceRole
from app.models.agents import AgentRun
from app.models.catalog import Product, WorkspaceProduct
from app.models.identity import User
from app.providers.embeddings import EmbeddingProvider
from app.services.agents import PRODUCT_RESEARCH, RULES_ENGINE, VALUE, AgentService
from app.services.workspaces import WorkspaceService

COMPARISON = "comparison"
_STATUS = {"MET": CellStatus.MET, "UNMET": CellStatus.UNMET}


def _research_cells(research: dict[str, Any] | None) -> tuple[dict[str, Cell], dict[str, str]]:
    cells: dict[str, Cell] = {}
    labels: dict[str, str] = {}
    for fact in (research or {}).get("facts", []):
        key = fact["key"]
        labels[key] = fact.get("label", key)
        number = fact.get("value_number")
        cells[key] = Cell(
            status=_STATUS.get(fact.get("status", ""), CellStatus.UNKNOWN),
            number=Decimal(str(number)) if number is not None else None,
            text=fact.get("value_text"),
            citations=tuple(fact.get("citations", [])),
        )
    return cells, labels


def _price_cell(value: dict[str, Any] | None) -> Cell:
    price = (value or {}).get("price")
    if not price:
        return Cell(CellStatus.UNKNOWN)
    fit = (value or {}).get("budget_fit")
    status = CellStatus.UNMET if fit in ("OVER", "UNDER_MIN") else CellStatus.MET
    if fit == "CURRENCY_MISMATCH":
        status = CellStatus.UNKNOWN
    return Cell(
        status=status,
        number=Decimal(str(price["amount"])),
        text=price.get("currency"),
        citations=tuple(price.get("citations", [])),
    )


class ComparisonService:
    def __init__(
        self, session: AsyncSession, settings: Settings, embedder: EmbeddingProvider
    ) -> None:
        self.session = session
        self.agents = AgentService(session, settings, embedder, None)

    async def compare(
        self,
        workspace_id: uuid.UUID,
        user: User,
        *,
        product_ids: list[uuid.UUID] | None,
        weights: dict[str, float],
        budget_is_hard: bool,
    ) -> AgentRun:
        started = time.perf_counter()
        await WorkspaceService(self.session).authorize(workspace_id, user, WorkspaceRole.MEMBER)
        rows = list(
            await self.session.execute(
                select(Product)
                .join(WorkspaceProduct, WorkspaceProduct.product_id == Product.id)
                .where(WorkspaceProduct.workspace_id == workspace_id)
                .order_by(WorkspaceProduct.created_at, Product.id)
            )
        )
        products = [row[0] for row in rows]
        if product_ids:
            wanted = set(product_ids)
            if wanted - {p.id for p in products}:
                raise WorkspaceProductNotFoundError
            products = [p for p in products if p.id in wanted]
        spec, requirement_version = await self.agents.current_spec(workspace_id)
        criteria = list(spec.criteria) if spec else []
        has_budget = bool(spec and spec.budget)
        if not products or not (criteria or has_budget):
            raise NothingToCompareError

        candidates: list[Candidate] = []
        labels: dict[str, str] = {}
        sources: dict[str, dict[str, str]] = {}
        for product in products:
            research = await self.agents.latest_run(
                workspace_id, product.id, PRODUCT_RESEARCH, requirement_version
            )
            value = await self.agents.latest_run(
                workspace_id, product.id, VALUE, requirement_version
            )
            cells, found_labels = _research_cells(research.output if research else None)
            labels |= found_labels
            if has_budget:
                cells[PRICE_KEY] = _price_cell(value.output if value else None)
            candidates.append(Candidate(str(product.id), f"{product.brand} {product.name}", cells))
            sources[str(product.id)] = {
                k: str(r.id) for k, r in (("research", research), ("value", value)) if r
            }

        columns = columns_for(
            criteria, weights, labels, include_price=has_budget, budget_is_hard=budget_is_hard
        )
        result: ComparisonResult = compare(columns, candidates)
        output = result.model_dump(mode="json") | {"source_runs": sources}
        return await self.agents.record(
            workspace_id,
            user,
            COMPARISON,
            None,
            None,
            requirement_version,
            AgentResult(
                output=_Output.model_validate(output),
                validation=validate_citations("", {}),
                engine=RULES_ENGINE,
            ),
            started,
        )


class _Output(ComparisonResult):
    source_runs: dict[str, dict[str, str]]
