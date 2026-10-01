"""Price observations and history for catalog products."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CacheDep, CurrentUser, SessionDep
from app.schemas.prices import PriceBatchIn, PriceBatchResult, PriceHistoryOut
from app.services.prices import Bucket, PriceService

router = APIRouter(prefix="/products/{product_id}/prices", tags=["prices"])
CACHE_NAMESPACE = "prices"


@router.post(
    "",
    response_model=PriceBatchResult,
    status_code=status.HTTP_201_CREATED,
    summary="Record price observations (idempotent per retailer/currency/timestamp)",
)
async def record_prices(
    product_id: uuid.UUID,
    body: PriceBatchIn,
    user: CurrentUser,
    session: SessionDep,
    cache: CacheDep,
) -> PriceBatchResult:
    result = await PriceService(session).record(product_id, user, body)
    if result.inserted:
        await cache.invalidate(CACHE_NAMESPACE, str(product_id))
    return result


@router.get(
    "",
    response_model=PriceHistoryOut,
    summary="Price history (bucketed per retailer) and statistics",
    description="Cached in Redis for PRICE_CACHE_TTL_SECONDS; recording new prices "
    "invalidates it. `X-Cache: HIT|MISS` tells which.",
)
async def price_history(
    product_id: uuid.UUID,
    _user: CurrentUser,
    session: SessionDep,
    cache: CacheDep,
    response: Response,
    currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
    bucket: Bucket = "day",
    since: datetime | None = None,
    until: datetime | None = None,
) -> PriceHistoryOut:
    params = f"{currency}|{bucket}|{since and since.isoformat()}|{until and until.isoformat()}"
    history, hit = await cache.get_or_load(
        CACHE_NAMESPACE,
        str(product_id),
        params,
        PriceHistoryOut,
        lambda: PriceService(session).history(
            product_id, currency=currency, bucket=bucket, since=since, until=until
        ),
    )
    response.headers["X-Cache"] = "HIT" if hit else "MISS"
    return history
