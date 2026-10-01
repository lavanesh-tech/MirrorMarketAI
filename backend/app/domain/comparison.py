"""Comparison engine: weighted multi-criteria scoring with hard constraints.

Inputs are plain values (no I/O): per product, a cell per criterion holding the
researched value and status. Rules:
- Hard constraints: a MUST criterion that is UNMET makes the product ineligible;
  over budget does too when the budget is hard. Ineligible products are still
  scored (so users can see by how much they lost) but always ranked last.
- Utility per cell, in [0, 1]:
    MET/UNMET contributes 0.7 (met) or 0; for numeric criteria the remaining 0.3
    is min-max normalised across the compared products in the criterion's direction
    (>= : higher is better, <= : lower is better). Unknown values score 0.
- Score = 100 x sum(weight x utility) / sum(weight). MUST criteria weigh 5 unless
  the caller overrides the weight.
- Winner: the best eligible product whose hard constraints are all verified
  (an unknown MUST value can't win; it is reported in `unknown_hard`).
- Sensitivity: each weight is halved and doubled in turn; any criterion whose
  change alters the winner is reported, and `stable` is True when none does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

from app.domain.requirements import Criterion, Operator, Priority

MET_SHARE = 0.7
RANGE_SHARE = 0.3
MUST_WEIGHT = 5
PRICE_KEY = "price"
PRICE_WEIGHT = 3
SENSITIVITY_FACTORS = (0.5, 2.0)


class CellStatus(StrEnum):
    MET = "MET"
    UNMET = "UNMET"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Cell:
    status: CellStatus
    number: Decimal | None = None
    text: str | None = None
    citations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Column:
    """One criterion in the matrix (requirement criteria plus an optional price row)."""

    key: str
    label: str
    weight: float
    hard: bool
    higher_is_better: bool | None  # None = not numeric / no direction


@dataclass(slots=True)
class Candidate:
    product_id: str
    name: str
    cells: dict[str, Cell] = field(default_factory=dict)


class CellResult(BaseModel):
    key: str
    status: CellStatus
    value: str | None
    utility: float
    contribution: float  # points added to the 0-100 score
    citations: list[str]


class ProductResult(BaseModel):
    product_id: str
    name: str
    eligible: bool
    violations: list[str]
    unknown_hard: list[str]
    score: float
    rank: int
    cells: list[CellResult]


class Sensitivity(BaseModel):
    stable: bool
    winner_changes_with: list[str]  # "<key> x0.5" / "<key> x2"


class ComparisonResult(BaseModel):
    criteria: list[dict[str, object]]
    products: list[ProductResult]
    winner_product_id: str | None
    margin: float | None  # winner score minus runner-up score (eligible only)
    sensitivity: Sensitivity


def columns_for(
    criteria: list[Criterion],
    weight_overrides: dict[str, float],
    labels: dict[str, str],
    *,
    include_price: bool,
    budget_is_hard: bool,
) -> list[Column]:
    cols: list[Column] = []
    for c in criteria:
        default = MUST_WEIGHT if c.priority is Priority.MUST else c.weight
        direction = None
        if c.value_number is not None and c.operator in (Operator.GTE, Operator.LTE):
            direction = c.operator is Operator.GTE
        cols.append(
            Column(
                key=c.key,
                label=labels.get(c.key, c.key),
                weight=float(weight_overrides.get(c.key, default)),
                hard=c.priority is Priority.MUST,
                higher_is_better=direction,
            )
        )
    if include_price:
        cols.append(
            Column(
                key=PRICE_KEY,
                label="Price",
                weight=float(weight_overrides.get(PRICE_KEY, PRICE_WEIGHT)),
                hard=budget_is_hard,
                higher_is_better=False,
            )
        )
    return cols


def _ranges(
    columns: list[Column], candidates: list[Candidate]
) -> dict[str, tuple[Decimal, Decimal]]:
    ranges: dict[str, tuple[Decimal, Decimal]] = {}
    for col in columns:
        values = [
            cell.number
            for cand in candidates
            if (cell := cand.cells.get(col.key)) is not None and cell.number is not None
        ]
        if values:
            ranges[col.key] = (min(values), max(values))
    return ranges


def _utility(col: Column, cell: Cell, bounds: tuple[Decimal, Decimal] | None) -> float:
    if cell.status is CellStatus.UNKNOWN:
        return 0.0
    met = MET_SHARE if cell.status is CellStatus.MET else 0.0
    if col.higher_is_better is None or cell.number is None or bounds is None:
        return met / MET_SHARE if met else 0.0  # boolean/text criteria: all or nothing
    low, high = bounds
    if high == low:
        position = 1.0
    else:
        position = float((cell.number - low) / (high - low))
        if not col.higher_is_better:
            position = 1.0 - position
    return round(met + RANGE_SHARE * position, 6)


def _score(
    columns: list[Column], candidates: list[Candidate]
) -> dict[str, tuple[float, list[CellResult]]]:
    ranges = _ranges(columns, candidates)
    total_weight = sum(c.weight for c in columns) or 1.0
    scored: dict[str, tuple[float, list[CellResult]]] = {}
    for cand in candidates:
        cells: list[CellResult] = []
        points = 0.0
        for col in columns:
            cell = cand.cells.get(col.key, Cell(CellStatus.UNKNOWN))
            utility = _utility(col, cell, ranges.get(col.key))
            contribution = 100 * col.weight * utility / total_weight
            points += contribution
            shown = format(cell.number.normalize(), "f") if cell.number is not None else cell.text
            cells.append(
                CellResult(
                    key=col.key,
                    status=cell.status,
                    value=shown,
                    utility=round(utility, 4),
                    contribution=round(contribution, 2),
                    citations=list(cell.citations),
                )
            )
        scored[cand.product_id] = (round(points, 2), cells)
    return scored


def _order(
    columns: list[Column], candidates: list[Candidate]
) -> list[tuple[Candidate, float, bool]]:
    scored = _score(columns, candidates)
    rows = []
    for cand in candidates:
        eligible = not any(
            col.hard
            and cand.cells.get(col.key, Cell(CellStatus.UNKNOWN)).status is CellStatus.UNMET
            for col in columns
        )
        rows.append((cand, scored[cand.product_id][0], eligible))
    return sorted(rows, key=lambda r: (not r[2], -r[1], r[0].name, r[0].product_id))


def _verified(columns: list[Column], cand: Candidate) -> bool:
    return not any(
        col.hard and cand.cells.get(col.key, Cell(CellStatus.UNKNOWN)).status is CellStatus.UNKNOWN
        for col in columns
    )


def _winner(columns: list[Column], candidates: list[Candidate]) -> str | None:
    """Best eligible product whose hard constraints are all verified (else no winner)."""
    for cand, _, eligible in _order(columns, candidates):
        if eligible and _verified(columns, cand):
            return cand.product_id
    return None


def _reweighted_winner(
    weights: list[float],
    utilities: list[list[float]],
    candidates: list[Candidate],
    eligible: dict[str, bool],
    verified: list[bool],
) -> str | None:
    total = sum(weights) or 1.0
    rows = [
        (
            not eligible[cand.product_id],
            -round(100 * sum(w * u for w, u in zip(weights, row, strict=True)) / total, 2),
            cand.name,
            cand.product_id,
            ok,
        )
        for cand, row, ok in zip(candidates, utilities, verified, strict=True)
    ]
    for ineligible, _, _, product_id, ok in sorted(rows):
        if not ineligible and ok:
            return product_id
    return None


def compare(columns: list[Column], candidates: list[Candidate]) -> ComparisonResult:
    scored = _score(columns, candidates)
    ordered = _order(columns, candidates)
    products: list[ProductResult] = []
    for rank, (cand, score, eligible) in enumerate(ordered, start=1):
        violations = [
            col.key
            for col in columns
            if col.hard
            and cand.cells.get(col.key, Cell(CellStatus.UNKNOWN)).status is CellStatus.UNMET
        ]
        unknown_hard = [
            col.key
            for col in columns
            if col.hard
            and cand.cells.get(col.key, Cell(CellStatus.UNKNOWN)).status is CellStatus.UNKNOWN
        ]
        products.append(
            ProductResult(
                product_id=cand.product_id,
                name=cand.name,
                eligible=eligible,
                violations=violations,
                unknown_hard=unknown_hard,
                score=score,
                rank=rank,
                cells=scored[cand.product_id][1],
            )
        )

    eligible_rows = [p for p in products if p.eligible]
    winner = _winner(columns, candidates)
    margin = (
        round(eligible_rows[0].score - eligible_rows[1].score, 2)
        if len(eligible_rows) > 1
        else None
    )
    flips: list[str] = []
    if winner is not None:
        # Utilities don't depend on weights, so re-ranking only re-weights a cached matrix.
        ranges = _ranges(columns, candidates)
        utilities = [
            [
                _utility(
                    col, cand.cells.get(col.key, Cell(CellStatus.UNKNOWN)), ranges.get(col.key)
                )
                for col in columns
            ]
            for cand in candidates
        ]
        flags = {p.product_id: p.eligible for p in products}
        verified = [_verified(columns, cand) for cand in candidates]
        base = [col.weight for col in columns]
        for i, col in enumerate(columns):
            for factor in SENSITIVITY_FACTORS:
                weights = [*base]
                weights[i] *= factor
                if _reweighted_winner(weights, utilities, candidates, flags, verified) != winner:
                    flips.append(f"{col.key} x{factor:g}")
    return ComparisonResult(
        criteria=[
            {"key": c.key, "label": c.label, "weight": c.weight, "hard": c.hard} for c in columns
        ],
        products=products,
        winner_product_id=winner,
        margin=margin,
        sensitivity=Sensitivity(stable=not flips, winner_changes_with=flips),
    )
