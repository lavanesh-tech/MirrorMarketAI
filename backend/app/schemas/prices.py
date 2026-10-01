"""Price history contracts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from app.domain.prices import PriceStats

MAX_BATCH = 100
MAX_FUTURE_SKEW = timedelta(minutes=5)


class PriceObservationIn(BaseModel):
    retailer: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    observed_at: datetime
    in_stock: bool | None = None
    url: HttpUrl | None = None
    variant_id: uuid.UUID | None = None

    @field_validator("retailer")
    @classmethod
    def _normalize_retailer(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("observed_at")
    @classmethod
    def _aware_and_not_future(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        if value > datetime.now(UTC) + MAX_FUTURE_SKEW:
            raise ValueError("observed_at cannot be in the future")
        return value


class PriceBatchIn(BaseModel):
    observations: list[PriceObservationIn] = Field(min_length=1, max_length=MAX_BATCH)
    source: Literal["MANUAL", "IMPORT"] = "MANUAL"


class PriceBatchResult(BaseModel):
    received: int
    inserted: int
    duplicates: int


class PricePoint(BaseModel):
    bucket: datetime
    low: Decimal
    high: Decimal
    observations: int


class RetailerSeries(BaseModel):
    retailer: str
    points: list[PricePoint]


class PriceHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: uuid.UUID
    currency: str
    bucket: str
    series: list[RetailerSeries]
    stats: PriceStats
