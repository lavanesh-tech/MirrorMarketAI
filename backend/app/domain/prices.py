"""Price statistics over a product's history (pure; Decimal throughout)."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel

CENT = Decimal("0.01")
WINDOW_DAYS = 30


@dataclass(frozen=True, slots=True)
class Observation:
    retailer: str
    amount: Decimal
    observed_at: datetime
    in_stock: bool | None = None


class RetailerPrice(BaseModel):
    retailer: str
    amount: Decimal
    observed_at: datetime
    in_stock: bool | None


class PriceStats(BaseModel):
    currency: str
    observations: int
    current: list[RetailerPrice]  # latest price per retailer, cheapest first
    lowest_current: RetailerPrice | None
    all_time_low: Decimal | None
    all_time_high: Decimal | None
    window_days: int
    window_average: Decimal | None
    window_low: Decimal | None
    change_pct: float | None  # lowest current vs. cheapest observation ~window_days ago
    volatility: float | None  # stdev / mean over the window (coefficient of variation)
    lowest_in_window: bool  # the cheapest current price is the window's low


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def summarize(currency: str, history: Sequence[Observation], now: datetime) -> PriceStats:
    latest: dict[str, Observation] = {}
    for obs in sorted(history, key=lambda o: o.observed_at):
        latest[obs.retailer] = obs
    current = sorted(
        (
            RetailerPrice(
                retailer=o.retailer, amount=o.amount, observed_at=o.observed_at, in_stock=o.in_stock
            )
            for o in latest.values()
            if o.in_stock is not False
        ),
        key=lambda p: (p.amount, p.retailer),
    )
    lowest = current[0] if current else None
    amounts = [o.amount for o in history]
    start = now - timedelta(days=WINDOW_DAYS)
    window = [o.amount for o in history if o.observed_at >= start]
    before = [o for o in history if o.observed_at < start]
    baseline = None
    if before:
        last_before = max(o.observed_at for o in before)  # computed once: O(n), not O(n^2)
        baseline = min(o.amount for o in before if o.observed_at == last_before)
    change = (
        round(float((lowest.amount - baseline) / baseline) * 100, 2)
        if lowest and baseline
        else None
    )
    volatility = None
    if len(window) > 1:
        mean = statistics.fmean(float(a) for a in window)
        volatility = round(statistics.pstdev(float(a) for a in window) / mean, 4)
    return PriceStats(
        currency=currency,
        observations=len(history),
        current=current,
        lowest_current=lowest,
        all_time_low=min(amounts) if amounts else None,
        all_time_high=max(amounts) if amounts else None,
        window_days=WINDOW_DAYS,
        window_average=_money(sum(window, Decimal(0)) / len(window)) if window else None,
        window_low=min(window) if window else None,
        change_pct=change,
        volatility=volatility,
        lowest_in_window=bool(lowest and window and lowest.amount <= min(window)),
    )
