"""Catalog and workspace-product repositories."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.domain.products import IdentifierScheme
from app.models.catalog import (
    Product,
    ProductIdentifier,
    ProductSpecification,
    ProductVariant,
    WorkspaceProduct,
)
from app.repositories.base import Page, PageRequest, Repository

_DETAIL = (
    selectinload(Product.variants),
    selectinload(Product.identifiers),
    selectinload(Product.specifications),
)


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ProductRepository(Repository[Product]):
    model = Product

    async def get_detail(self, product_id: uuid.UUID) -> Product | None:
        product: Product | None = await self.session.scalar(
            select(Product).options(*_DETAIL).where(Product.id == product_id)
        )
        return product

    async def get_by_canonical_key(self, key: str) -> Product | None:
        product: Product | None = await self.session.scalar(
            select(Product).where(Product.canonical_key == key)
        )
        return product

    async def search(
        self, *, query: str | None, category: str | None, page: PageRequest
    ) -> Page[Product]:
        stmt = select(Product)
        if query:
            pattern = f"%{_escape_like(query)}%"
            stmt = stmt.where(
                or_(
                    Product.brand.ilike(pattern, escape="\\"),
                    Product.name.ilike(pattern, escape="\\"),
                )
            )
        if category:
            stmt = stmt.where(Product.category == category)
        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = await self.session.scalars(
            stmt.order_by(Product.brand, Product.name, Product.id)
            .limit(page.limit)
            .offset(page.offset)
        )
        return Page(items=list(rows), total=total, limit=page.limit, offset=page.offset)

    async def find_by_identifier(self, scheme: IdentifierScheme, value: str) -> Product | None:
        product: Product | None = await self.session.scalar(
            select(Product)
            .options(*_DETAIL)
            .join(ProductIdentifier, ProductIdentifier.product_id == Product.id)
            .where(ProductIdentifier.scheme == scheme, ProductIdentifier.value == value)
        )
        return product

    async def identifier_exists(self, scheme: IdentifierScheme, value: str) -> bool:
        found = await self.session.scalar(
            select(ProductIdentifier.id).where(
                ProductIdentifier.scheme == scheme, ProductIdentifier.value == value
            )
        )
        return found is not None

    async def get_variant(
        self, product_id: uuid.UUID, variant_id: uuid.UUID
    ) -> ProductVariant | None:
        variant: ProductVariant | None = await self.session.scalar(
            select(ProductVariant).where(
                ProductVariant.id == variant_id, ProductVariant.product_id == product_id
            )
        )
        return variant

    async def get_specification(
        self, product_id: uuid.UUID, variant_id: uuid.UUID | None, key: str
    ) -> ProductSpecification | None:
        stmt = select(ProductSpecification).where(
            ProductSpecification.product_id == product_id, ProductSpecification.key == key
        )
        stmt = stmt.where(
            ProductSpecification.variant_id.is_(None)
            if variant_id is None
            else ProductSpecification.variant_id == variant_id
        )
        spec: ProductSpecification | None = await self.session.scalar(stmt)
        return spec


class WorkspaceProductRepository(Repository[WorkspaceProduct]):
    model = WorkspaceProduct

    async def get_in_workspace(
        self, workspace_id: uuid.UUID, product_id: uuid.UUID
    ) -> WorkspaceProduct | None:
        item: WorkspaceProduct | None = await self.session.scalar(
            select(WorkspaceProduct).where(
                WorkspaceProduct.workspace_id == workspace_id,
                WorkspaceProduct.product_id == product_id,
            )
        )
        return item

    async def list_for_workspace(self, workspace_id: uuid.UUID) -> list[WorkspaceProduct]:
        rows = await self.session.scalars(
            select(WorkspaceProduct)
            .where(WorkspaceProduct.workspace_id == workspace_id)
            .order_by(WorkspaceProduct.created_at, WorkspaceProduct.id)
        )
        return list(rows)
