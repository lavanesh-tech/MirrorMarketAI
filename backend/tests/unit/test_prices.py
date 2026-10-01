"""Price statistics and observation validation (pure)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.prices import Observation, summarize
from app.schemas.prices import PriceObservationIn

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def _o(retailer: str, amount: str, days_ago: int, in_stock: bool | None = None) -> Observation:
    return Observation(retailer, Decimal(amount), NOW - timedelta(days=days_ago), in_stock)


def test_summarize_current_lows_and_change() -> None:
    history = [
        _o("Shop A", "1499.00", 60),
        _o("Shop B", "1449.00", 60),
        _o("Shop A", "1399.00", 20),
        _o("Shop A", "1299.00", 1),
        _o("Shop B", "1349.00", 2),
        _o("Shop C", "1199.00", 3, in_stock=False),
    ]
    stats = summarize("USD", history, NOW)
    assert [(p.retailer, p.amount) for p in stats.current] == [
        ("Shop A", Decimal("1299.00")),
        ("Shop B", Decimal("1349.00")),
    ]  # out-of-stock Shop C is excluded from current prices
    assert stats.lowest_current is not None
    assert stats.lowest_current.retailer == "Shop A"
    assert (stats.all_time_low, stats.all_time_high) == (Decimal("1199.00"), Decimal("1499.00"))
    assert stats.window_low == Decimal("1199.00")
    assert stats.window_average == Decimal("1311.50")
    assert stats.change_pct == round((1299 - 1449) / 1449 * 100, 2)
    assert stats.lowest_in_window is False  # 1199 out of stock was lower
    assert stats.volatility is not None
    assert stats.volatility > 0


def test_summarize_edge_cases() -> None:
    empty = summarize("EUR", [], NOW)
    assert (empty.lowest_current, empty.change_pct, empty.volatility, empty.window_average) == (
        None,
        None,
        None,
        None,
    )
    single = summarize("USD", [_o("Only", "10.00", 0)], NOW)
    assert single.lowest_in_window is True
    assert single.volatility is None
    assert single.change_pct is None


def test_observation_validation() -> None:
    ok = PriceObservationIn(
        retailer="  Best   Shop ", amount=Decimal("9.99"), currency="USD", observed_at=NOW
    )
    assert ok.retailer == "Best Shop"
    for bad in (
        {"amount": Decimal(0)},
        {"currency": "usd"},
        {"observed_at": datetime(2026, 1, 1)},  # noqa: DTZ001 - naive on purpose
        {"observed_at": datetime.now(UTC) + timedelta(days=1)},
        {"amount": Decimal("1.999")},
    ):
        with pytest.raises(ValidationError):
            PriceObservationIn.model_validate(
                {"retailer": "x", "amount": "1", "currency": "USD", "observed_at": NOW} | bad
            )
