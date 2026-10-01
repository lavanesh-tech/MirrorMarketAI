"""Price observations and history for catalog products."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, SessionDep
from app.schemas.prices import PriceBatchIn, PriceBatchResult, PriceHistoryOut
from app.services.prices import Bucket, PriceService

router = APIRouter(prefix="/products/{product_id}/prices", tags=["prices"])


@router.post(
    "",
    response_model=PriceBatchResult,
    status_code=status.HTTP_201_CREATED,
    summary="Record price observations (idempotent per retailer/currency/timestamp)",
)
async def record_prices(
    product_id: uuid.UUID, body: PriceBatchIn, user: CurrentUser, session: SessionDep
) -> PriceBatchResult:
    return await PriceService(session).record(product_id, user, body)


@router.get(
    "",
    response_model=PriceHistoryOut,
    summary="Price history (bucketed per retailer) and statistics",
)
async def price_history(
    product_id: uuid.UUID,
    _user: CurrentUser,
    session: SessionDep,
    currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
    bucket: Bucket = "day",
    since: datetime | None = None,
    until: datetime | None = None,
) -> PriceHistoryOut:
    return await PriceService(session).history(
        product_id, currency=currency, bucket=bucket, since=since, until=until
    )
