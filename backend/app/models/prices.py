"""Price observations: one row per (product, retailer, currency, observed_at).

Prices are public facts about catalog products, so snapshots are global (not
workspace-scoped). Amounts are NUMERIC with an explicit ISO-4217 currency.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

PRICE_SOURCES = ("MANUAL", "IMPORT", "EVIDENCE")


class PriceSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "price_snapshots"
    __table_args__ = (
        UniqueConstraint("product_id", "retailer", "currency", "observed_at"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso"),
        CheckConstraint(
            "source IN (" + ", ".join(f"'{s}'" for s in PRICE_SOURCES) + ")", name="source_valid"
        ),
        Index("ix_price_snapshots_history", "product_id", "currency", "observed_at"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="SET NULL"), index=True
    )
    retailer: Mapped[str] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    in_stock: Mapped[bool | None]
    url: Mapped[str | None] = mapped_column(String(2048))
    source: Mapped[str] = mapped_column(String(16))
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
