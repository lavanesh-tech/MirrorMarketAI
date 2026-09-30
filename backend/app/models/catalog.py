"""Product catalog tables and the workspace <-> product link.

Products are *global* catalog entries (the same laptop can be compared in many
workspaces). What a workspace is comparing lives in `workspace_products`, which
is the tenant-scoped table.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.products import IdentifierScheme
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

PRODUCT_CATEGORIES = (
    "laptop",
    "desktop",
    "monitor",
    "phone",
    "tablet",
    "headphones",
    "camera",
    "appliance",
    "other",
)


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("canonical_key"),
        CheckConstraint(
            "category IN (" + ", ".join(f"'{c}'" for c in PRODUCT_CATEGORIES) + ")",
            name="category_valid",
        ),
    )

    brand: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    # lower-cased, whitespace-collapsed "brand::name"; blocks case-variant duplicates.
    canonical_key: Mapped[str] = mapped_column(String(310))
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )

    variants: Mapped[list[ProductVariant]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ProductVariant.name",
    )
    identifiers: Mapped[list[ProductIdentifier]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="(ProductIdentifier.scheme, ProductIdentifier.value)",
    )
    specifications: Mapped[list[ProductSpecification]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ProductSpecification.key",
    )


class ProductVariant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_variants"
    __table_args__ = (UniqueConstraint("product_id", "name"),)

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))  # e.g. "16 GB / 512 GB, Midnight"
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")

    product: Mapped[Product] = relationship(back_populates="variants")


class ProductIdentifier(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_identifiers"
    __table_args__ = (
        # An identifier points at exactly one product, catalog-wide.
        UniqueConstraint("scheme", "value"),
        CheckConstraint(
            "scheme IN (" + ", ".join(f"'{s.value}'" for s in IdentifierScheme) + ")",
            name="scheme_valid",
        ),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), index=True
    )
    scheme: Mapped[IdentifierScheme] = mapped_column(
        Enum(
            IdentifierScheme,
            native_enum=False,
            create_constraint=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
            validate_strings=True,
        )
    )
    value: Mapped[str] = mapped_column(String(64))


class ProductSpecification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One technical fact, e.g. key=ram_gb value_number=16 unit=GB.

    Exactly one of value_number / value_text is set, so numeric specs can be
    compared and scored by deterministic code (Phase 16) without parsing text.
    Provenance links to evidence sources arrive in Phase 5.
    """

    __tablename__ = "product_specifications"
    __table_args__ = (
        # One value per key per product (or per variant). NULLS NOT DISTINCT
        # makes product-level specs (variant_id IS NULL) unique too.
        UniqueConstraint("product_id", "variant_id", "key", postgresql_nulls_not_distinct=True),
        CheckConstraint("(value_number IS NULL) <> (value_text IS NULL)", name="exactly_one_value"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(64))
    value_number: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    value_text: Mapped[str | None] = mapped_column(String(500))
    unit: Mapped[str | None] = mapped_column(String(16))


class WorkspaceProduct(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A product being compared inside a workspace (tenant-scoped)."""

    __tablename__ = "workspace_products"
    __table_args__ = (UniqueConstraint("workspace_id", "product_id"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comparison_workspaces.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="SET NULL")
    )
    added_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)

    product: Mapped[Product] = relationship(lazy="joined")
