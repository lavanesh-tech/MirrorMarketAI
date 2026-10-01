"""Value Agent arithmetic (no database)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.agents.value import (
    BudgetFit,
    Price,
    budget_fit,
    build_output,
    catalog_price,
    cost_per_unit,
    requirement_fit,
)
from app.domain.citations import validate_citations
from app.domain.requirements import Budget, Criterion, Operator, Priority, RequirementSpec
from app.models.catalog import Product, ProductSpecification
from benchmarks.price_extraction import run

pytestmark = pytest.mark.unit

USD = Price(amount=Decimal(1200), currency="USD", source="evidence", citations=["E1"], quote="x")


def _c(key: str, priority: Priority = Priority.SHOULD, weight: int = 3) -> Criterion:
    return Criterion(
        key=key, operator=Operator.GTE, value_number=Decimal(1), priority=priority, weight=weight
    )


RESEARCH: dict[str, Any] = {
    "facts": [
        {"key": "ram_gb", "status": "MET", "value_number": "16"},
        {"key": "weight_kg", "status": "UNMET", "value_number": "1.4"},
        {"key": "storage_gb", "status": "UNKNOWN", "value_number": None},
        {"key": "has_thunderbolt", "status": "MET", "value_number": None},
        {"key": "battery_life_hours", "status": "NO_REQUIREMENT", "value_number": "0"},
    ]
}


@pytest.mark.parametrize(
    ("price", "budget", "expected"),
    [
        (USD, None, (BudgetFit.NO_BUDGET, None)),
        (None, Budget(max_amount=Decimal(1500)), (BudgetFit.UNKNOWN_PRICE, None)),
        (
            USD,
            Budget(max_amount=Decimal(1500), currency="EUR"),
            (BudgetFit.CURRENCY_MISMATCH, None),
        ),
        (USD, Budget(max_amount=Decimal(1000)), (BudgetFit.OVER, Decimal("200.00"))),
        (USD, Budget(min_amount=Decimal(1500)), (BudgetFit.UNDER_MIN, None)),
        (USD, Budget(min_amount=Decimal(1000), max_amount=Decimal(1200)), (BudgetFit.WITHIN, None)),
    ],
)
def test_budget_fit(
    price: Price | None, budget: Budget | None, expected: tuple[BudgetFit, Decimal | None]
) -> None:
    assert budget_fit(price, budget) == expected


def test_requirement_fit_weights_must_as_five() -> None:
    spec = RequirementSpec(
        criteria=[
            _c("ram_gb", Priority.MUST),
            _c("weight_kg", weight=3),
            _c("storage_gb", weight=2),
            _c("has_thunderbolt", weight=1),
        ]
    )
    assert requirement_fit(spec, RESEARCH) == round(6 / 11, 4)
    assert requirement_fit(spec, None) is None
    assert requirement_fit(RequirementSpec(), RESEARCH) is None
    assert requirement_fit(None, RESEARCH) is None


def test_cost_per_unit_skips_booleans_weight_and_zero() -> None:
    assert cost_per_unit(USD, RESEARCH) == {"usd_per_ram_gb": Decimal("75.00")}
    assert cost_per_unit(None, RESEARCH) == {}


def test_catalog_price() -> None:
    specs = [
        ProductSpecification(
            key="price", value_number=Decimal(999), unit="eur", variant_id=uuid.uuid4()
        ),
        ProductSpecification(key="price", value_number=Decimal(1099), unit="eur"),
    ]
    found = catalog_price(specs)
    assert found is not None
    assert (found.amount, found.currency, found.source) == (Decimal(1099), "EUR", "catalog")
    assert catalog_price([ProductSpecification(key="price", value_text="call us")]) is None


def test_build_output_value_index_and_cited_summary() -> None:
    product = Product(id=uuid.uuid4(), brand="Acme", name="L14", category="laptop")
    spec = RequirementSpec(
        budget=Budget(max_amount=Decimal(1500)), criteria=[_c("ram_gb", Priority.MUST)]
    )
    price = Price(
        amount=Decimal("1199.99"), currency="USD", source="evidence", citations=["E2"], quote="q"
    )
    out = build_output(product, price, spec, RESEARCH, "run-1")
    assert out.budget_fit is BudgetFit.WITHIN
    assert out.requirement_fit == 1.0
    assert out.value_index == round(1 / (1199.99 / 1500), 4)
    assert out.summary == "Price: $1,199.99 [E2]."
    assert validate_citations(out.summary, {2: "Now $1,199.99 at most stores."}).valid
    whole = build_output(product, USD, spec, RESEARCH, None)
    assert whole.summary == "Price: $1,200 [E1]."
    catalog = Price(amount=Decimal(5), currency="CHF", source="catalog")
    no_cite = build_output(product, catalog, None, None, None)
    assert (no_cite.summary, no_cite.value_index, no_cite.budget_fit) == (
        "",
        None,
        BudgetFit.NO_BUDGET,
    )


def test_price_benchmark() -> None:
    result = run()
    assert result["cases"] == 14
    assert result["correct"] == 13  # known miss: two products priced in one sentence
