"""Record price observations and serve bucketed history with statistics."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProductNotFoundError
from app.domain.prices import Observation, summarize
from app.models.catalog import Product
from app.models.identity import User
from app.models.prices import PriceSnapshot
from app.schemas.prices import (
    PriceBatchIn,
    PriceBatchResult,
    PriceHistoryOut,
    PricePoint,
    RetailerSeries,
)

Bucket = Literal["day", "week", "month"]


class PriceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _product(self, product_id: uuid.UUID) -> Product:
        product = await self.session.get(Product, product_id)
        if product is None:
            raise ProductNotFoundError
        return product

    async def record(
        self, product_id: uuid.UUID, user: User, batch: PriceBatchIn
    ) -> PriceBatchResult:
        """Idempotent: an identical (retailer, currency, observed_at) row is skipped."""
        await self._product(product_id)
        rows = [
            {
                "id": uuid.uuid4(),
                "product_id": product_id,
                "variant_id": o.variant_id,
                "retailer": o.retailer,
                "amount": o.amount,
                "currency": o.currency,
                "observed_at": o.observed_at,
                "in_stock": o.in_stock,
                "url": str(o.url) if o.url else None,
                "source": batch.source,
                "created_by_id": user.id,
            }
            for o in batch.observations
        ]
        stmt = (
            insert(PriceSnapshot)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["product_id", "retailer", "currency", "observed_at"]
            )
            .returning(PriceSnapshot.id)
        )
        inserted = len(list(await self.session.scalars(stmt)))
        await self.session.commit()
        return PriceBatchResult(
            received=len(rows), inserted=inserted, duplicates=len(rows) - inserted
        )

    async def _default_currency(self, product_id: uuid.UUID) -> str | None:
        currency: str | None = await self.session.scalar(
            select(PriceSnapshot.currency)
            .where(PriceSnapshot.product_id == product_id)
            .group_by(PriceSnapshot.currency)
            .order_by(func.count().desc(), PriceSnapshot.currency)
            .limit(1)
        )
        return currency

    async def history(
        self,
        product_id: uuid.UUID,
        *,
        currency: str | None,
        bucket: Bucket,
        since: datetime | None,
        until: datetime | None,
    ) -> PriceHistoryOut:
        await self._product(product_id)
        currency = currency or await self._default_currency(product_id) or "USD"
        base = [PriceSnapshot.product_id == product_id, PriceSnapshot.currency == currency]
        window = list(base)
        if since:
            window.append(PriceSnapshot.observed_at >= since)
        if until:
            window.append(PriceSnapshot.observed_at <= until)

        # Bucketing happens in PostgreSQL (date_trunc + GROUP BY); the index on
        # (product_id, currency, observed_at) serves the range scan.
        bucket_col = func.date_trunc(bucket, PriceSnapshot.observed_at).label("bucket")
        rows = await self.session.execute(
            select(
                PriceSnapshot.retailer,
                bucket_col,
                func.min(PriceSnapshot.amount),
                func.max(PriceSnapshot.amount),
                func.count(),
            )
            .where(*window)
            .group_by(PriceSnapshot.retailer, bucket_col)
            .order_by(PriceSnapshot.retailer, bucket_col)
        )
        series: dict[str, list[PricePoint]] = {}
        for retailer, at, low, high, count in rows:
            series.setdefault(retailer, []).append(
                PricePoint(bucket=at, low=low, high=high, observations=count)
            )

        # Plain column tuples, not ORM entities: identity-map overhead dominated this query.
        rows = await self.session.execute(
            select(
                PriceSnapshot.retailer,
                PriceSnapshot.amount,
                PriceSnapshot.observed_at,
                PriceSnapshot.in_stock,
            ).where(*base)
        )
        observations = [Observation(*row) for row in rows]
        return PriceHistoryOut(
            product_id=product_id,
            currency=currency,
            bucket=bucket,
            series=[RetailerSeries(retailer=k, points=v) for k, v in series.items()],
            stats=summarize(currency, observations, datetime.now(UTC)),
        )
