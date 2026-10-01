"""Value Agent: price, budget fit and value-for-money, computed deterministically.

Pricing math must be exact and reproducible, so this agent has no LLM engine:
- price: the first priced sentence in product-filtered evidence (sale/"now" prices
  win over list/"was" prices in the same sentence), else a catalog `price` spec;
- budget fit against the current requirements (no currency conversion: a
  mismatch is reported, never guessed);
- requirement fit from the latest Product Research run for the same requirement
  version (MUST criteria weigh 5, SHOULD criteria use their 1-5 weight);
- value index = requirement fit / (price / budget max), plus price per unit of
  the researched numeric facts (e.g. USD per GB of RAM).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, field_validator

from app.domain.citations import split_sentences
from app.domain.requirements import Budget, Priority, RequirementSpec
from app.models.catalog import Product, ProductSpecification

PRICE_QUERY = "price or prices or priced or costs or retails or msrp or sale"
MUST_WEIGHT = 5
CENT = Decimal("0.01")

_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}
_PRICE = re.compile(
    r"([$€£])\s?(\d{1,3}(?:,\d{3})+|\d{1,3}(?:\.\d{3})+(?![\d.])|\d+)(?:\.(\d{2}))?(?!\d)"
)
_CONTEXT = re.compile(
    r"\b(price[ds]?|costs?|msrp|retails?|sells?|starting at|starts at|priced|"
    r"available for|buy|deal|sale)\b",
    re.I,
)
_OLD_PRICE = re.compile(r"\b(was|originally|list price|regularly|down from|reduced from)\s*$", re.I)


class BudgetFit(StrEnum):
    WITHIN = "WITHIN"
    OVER = "OVER"
    UNDER_MIN = "UNDER_MIN"
    NO_BUDGET = "NO_BUDGET"
    UNKNOWN_PRICE = "UNKNOWN_PRICE"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"


class Price(BaseModel):
    amount: Decimal
    currency: str
    source: str  # "evidence" | "catalog"
    citations: list[str] = []
    quote: str | None = None

    @field_validator("amount")
    @classmethod
    def _cents(cls, value: Decimal) -> Decimal:
        return value.quantize(CENT, rounding=ROUND_HALF_UP)


class ValueOutput(BaseModel):
    product_id: str
    summary: str
    price: Price | None
    budget: Budget | None
    budget_fit: BudgetFit
    over_budget_by: Decimal | None = None
    requirement_fit: float | None = None  # 0..1 weighted share of criteria MET
    research_run_id: str | None = None
    value_index: float | None = None
    cost_per_unit: dict[str, Decimal] = {}


def extract_price(text: str) -> tuple[Decimal, str, str] | None:
    """(amount, currency, sentence) from the first sentence with a price in a price context."""
    fallback: tuple[Decimal, str, str] | None = None
    for sentence in split_sentences(text):
        matches = list(_PRICE.finditer(sentence))
        if not matches:
            continue
        current = [m for m in matches if not _OLD_PRICE.search(sentence[: m.start()])]
        chosen = (current or matches)[0]
        symbol, whole, cents = chosen.groups()
        amount = Decimal(re.sub(r"[,.]", "", whole) + (f".{cents}" if cents else ""))
        found = (amount, _SYMBOLS[symbol], sentence)
        if _CONTEXT.search(sentence):
            return found
        fallback = fallback or found
    return fallback


def catalog_price(catalog: Sequence[ProductSpecification]) -> Price | None:
    for spec in catalog:
        if spec.variant_id is None and spec.key == "price" and spec.value_number is not None:
            currency = (spec.unit or "USD").upper()
            return Price(amount=spec.value_number, currency=currency, source="catalog")
    return None


def budget_fit(price: Price | None, budget: Budget | None) -> tuple[BudgetFit, Decimal | None]:
    if budget is None:
        return BudgetFit.NO_BUDGET, None
    if price is None:
        return BudgetFit.UNKNOWN_PRICE, None
    if price.currency != budget.currency:
        return BudgetFit.CURRENCY_MISMATCH, None
    if budget.max_amount is not None and price.amount > budget.max_amount:
        return BudgetFit.OVER, price.amount - budget.max_amount
    if budget.min_amount is not None and price.amount < budget.min_amount:
        return BudgetFit.UNDER_MIN, None
    return BudgetFit.WITHIN, None


def requirement_fit(spec: RequirementSpec | None, research: dict[str, Any] | None) -> float | None:
    """Weighted share of criteria MET in a Product Research output (UNKNOWN counts as not met)."""
    if spec is None or not spec.criteria or research is None:
        return None
    status = {f["key"]: f["status"] for f in research.get("facts", [])}
    total = met = 0
    for criterion in spec.criteria:
        weight = MUST_WEIGHT if criterion.priority is Priority.MUST else criterion.weight
        total += weight
        met += weight if status.get(criterion.key) == "MET" else 0
    return round(met / total, 4) if total else None


def cost_per_unit(price: Price | None, research: dict[str, Any] | None) -> dict[str, Decimal]:
    if price is None or research is None:
        return {}
    result: dict[str, Decimal] = {}
    for fact in research.get("facts", []):
        raw = fact.get("value_number")
        if raw is None or fact["key"].startswith(("has_", "is_")) or fact["key"] == "weight_kg":
            continue
        value = Decimal(str(raw))
        if value > 0:
            per = (price.amount / value).quantize(CENT, rounding=ROUND_HALF_UP)
            result[f"{price.currency.lower()}_per_{fact['key']}"] = per
    return result


def build_output(
    product: Product,
    price: Price | None,
    spec: RequirementSpec | None,
    research: dict[str, Any] | None,
    research_run_id: str | None,
) -> ValueOutput:
    budget = spec.budget if spec else None
    fit, over_by = budget_fit(price, budget)
    req_fit = requirement_fit(spec, research)
    value_index = None
    if (
        req_fit is not None
        and price is not None
        and budget is not None
        and budget.max_amount
        and fit is not BudgetFit.CURRENCY_MISMATCH
        and price.amount > 0
    ):
        value_index = round(req_fit / float(price.amount / budget.max_amount), 4)
    summary = ""
    if price is not None and price.citations:
        # Only the price is stated (it is in the cited item); fit and ratios are data fields.
        shown = f"{price.amount:,}" if price.amount % 1 else f"{int(price.amount):,}"
        symbol = {v: k for k, v in _SYMBOLS.items()}.get(price.currency, price.currency + " ")
        summary = f"Price: {symbol}{shown} [{price.citations[0]}]."
    return ValueOutput(
        product_id=str(product.id),
        summary=summary,
        price=price,
        budget=budget,
        budget_fit=fit,
        over_budget_by=over_by,
        requirement_fit=req_fit,
        research_run_id=research_run_id,
        value_index=value_index,
        cost_per_unit=cost_per_unit(price, research),
    )
