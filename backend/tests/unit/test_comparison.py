"""Comparison engine (pure)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.comparison import (
    PRICE_KEY,
    Candidate,
    Cell,
    CellStatus,
    columns_for,
    compare,
)
from app.domain.requirements import Criterion, Operator, Priority

pytestmark = pytest.mark.unit

M, U, X = CellStatus.MET, CellStatus.UNMET, CellStatus.UNKNOWN
CRITERIA = [
    Criterion(
        key="ram_gb", operator=Operator.GTE, value_number=Decimal(16), priority=Priority.MUST
    ),
    Criterion(key="weight_kg", operator=Operator.LTE, value_number=Decimal(2), weight=2),
    Criterion(key="has_oled_display", operator=Operator.EQ, value_text="yes", weight=1),
]


def _cand(
    pid: str,
    ram: tuple[CellStatus, int | None],
    weight: tuple[CellStatus, str | None],
    oled: CellStatus,
    price: tuple[CellStatus, int | None],
) -> Candidate:
    def num(status: CellStatus, value: int | str | None) -> Cell:
        return Cell(status, Decimal(str(value)) if value is not None else None, citations=("E1",))

    return Candidate(
        pid,
        f"Acme {pid}",
        {
            "ram_gb": num(*ram),
            "weight_kg": num(*weight),
            "has_oled_display": Cell(oled, text="yes" if oled is M else "no"),
            PRICE_KEY: num(*price),
        },
    )


A = _cand("A", (M, 16), (M, "1.2"), M, (M, 1200))
B = _cand("B", (M, 32), (M, "1.8"), U, (M, 1400))
C = _cand("C", (U, 8), (M, "1.0"), M, (M, 700))
D = _cand("D", (X, None), (X, None), X, (U, 1900))


def _cols(weights: dict[str, float] | None = None, *, hard_budget: bool = True):  # type: ignore[no-untyped-def]
    return columns_for(
        CRITERIA,
        weights or {},
        {"ram_gb": "Memory"},
        include_price=True,
        budget_is_hard=hard_budget,
    )


def test_columns_defaults_and_overrides() -> None:
    cols = {c.key: c for c in _cols({"weight_kg": 0})}
    assert (cols["ram_gb"].weight, cols["ram_gb"].hard, cols["ram_gb"].label) == (5, True, "Memory")
    assert (cols["weight_kg"].weight, cols["weight_kg"].higher_is_better) == (0, False)
    assert cols["has_oled_display"].higher_is_better is None
    assert (cols[PRICE_KEY].weight, cols[PRICE_KEY].hard) == (3, True)
    no_price = columns_for(CRITERIA, {}, {}, include_price=False, budget_is_hard=True)
    assert PRICE_KEY not in {c.key for c in no_price}


def test_scores_constraints_and_ranking() -> None:
    result = compare(_cols(), [A, B, C, D])
    by = {p.product_id: p for p in result.products}
    assert [p.product_id for p in result.products] == ["A", "B", "C", "D"]
    assert (by["C"].eligible, by["C"].violations) == (False, ["ram_gb"])
    assert (by["D"].eligible, by["D"].violations, by["D"].unknown_hard) == (
        False,
        [PRICE_KEY],
        ["ram_gb"],
    )
    assert result.winner_product_id == "A"
    # weights: ram 5, weight 2, oled 1, price 3 (total 11)
    ram_a = 0.7 + 0.3 * (16 - 8) / (32 - 8)
    weight_a = 0.7 + 0.3 * (1.8 - 1.2) / 0.8
    price_a = 0.7 + 0.3 * (1900 - 1200) / (1900 - 700)
    expected_a = round(100 * (5 * ram_a + 2 * weight_a + 1 * 1 + 3 * price_a) / 11, 2)
    assert by["A"].score == pytest.approx(expected_a, abs=0.01)
    oled_b = next(c for c in by["B"].cells if c.key == "has_oled_display")
    assert (oled_b.utility, oled_b.contribution) == (0.0, 0.0)
    assert by["D"].score == 0.0
    assert result.margin == round(by["A"].score - by["B"].score, 2)
    ram_cell = next(c for c in by["A"].cells if c.key == "ram_gb")
    assert (ram_cell.value, ram_cell.citations) == ("16", ["E1"])


def test_soft_budget_and_weight_overrides_change_the_winner() -> None:
    soft = compare(_cols(hard_budget=False), [A, D])
    assert soft.products[1].violations == []  # D only fails on unknown data, not a hard rule
    flipped = compare(
        _cols({"ram_gb": 10, PRICE_KEY: 0, "weight_kg": 0, "has_oled_display": 0}), [A, B]
    )
    assert flipped.winner_product_id == "B"


def test_sensitivity_and_degenerate_cases() -> None:
    close = compare(_cols(), [A, B])
    assert close.winner_product_id == "A"
    assert (close.products[0].score, close.products[1].score) == (86.36, 77.27)
    assert close.sensitivity.stable is False
    assert close.sensitivity.winner_changes_with == ["ram_gb x2"]  # B has twice the RAM
    same = compare(_cols(), [A, _cand("A2", (M, 16), (M, "1.2"), M, (M, 1200))])
    assert same.products[0].score == same.products[1].score
    assert same.products[0].product_id == "A"  # tie broken by name
    nobody = compare(_cols(), [C])
    assert (nobody.winner_product_id, nobody.margin, nobody.sensitivity.stable) == (
        None,
        None,
        True,
    )
    single_value = compare(_cols(), [A])
    assert single_value.products[0].cells[0].utility == 1.0  # max == min range counts as best
