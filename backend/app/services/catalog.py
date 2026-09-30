"""Product catalog and workspace-product use-cases.

Catalog edit policy (until moderation exists): any signed-in user can create a
product; only its creator can add variants, identifiers or specifications.
Workspace product lists follow workspace roles: EDITOR+ adds/removes, any
member reads.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    IdentifierAlreadyExistsError,
    InvalidIdentifierValueError,
    PermissionDeniedError,
    ProductAlreadyExistsError,
    ProductAlreadyInWorkspaceError,
    ProductNotFoundError,
    VariantAlreadyExistsError,
    VariantNotFoundError,
    WorkspaceProductNotFoundError,
)
from app.domain.products import (
    IdentifierScheme,
    InvalidIdentifierError,
    canonical_product_key,
    normalize_identifier,
)
from app.domain.roles import WorkspaceRole
from app.models.catalog import (
    Product,
    ProductIdentifier,
    ProductSpecification,
    ProductVariant,
    WorkspaceProduct,
)
from app.models.identity import User
from app.repositories.base import Page, PageRequest
from app.repositories.catalog import ProductRepository, WorkspaceProductRepository
from app.services.workspaces import WorkspaceService


@dataclass(frozen=True, slots=True)
class SpecInput:
    key: str
    value_number: Decimal | None
    value_text: str | None
    unit: str | None
    variant_id: uuid.UUID | None


class CatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.products = ProductRepository(session)

    async def _commit_or_conflict(self, conflict: type[Exception]) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:  # a concurrent writer won the unique index
            await self.session.rollback()
            raise conflict from exc

    async def _editable(self, product_id: uuid.UUID, user: User) -> Product:
        product = await self.products.get_detail(product_id)
        if product is None:
            raise ProductNotFoundError
        if product.created_by_id != user.id:
            raise PermissionDeniedError("Only the product's creator can edit it.")
        return product

    async def create_product(
        self, user: User, *, brand: str, name: str, category: str, description: str | None
    ) -> Product:
        key = canonical_product_key(brand, name)
        if await self.products.get_by_canonical_key(key) is not None:
            raise ProductAlreadyExistsError
        product = await self.products.add(
            Product(
                brand=brand,
                name=name,
                category=category,
                description=description,
                canonical_key=key,
                created_by_id=user.id,
            )
        )
        await self._commit_or_conflict(ProductAlreadyExistsError)
        return await self.get_product(product.id)

    async def get_product(self, product_id: uuid.UUID) -> Product:
        product = await self.products.get_detail(product_id)
        if product is None:
            raise ProductNotFoundError
        return product

    async def search(
        self, *, query: str | None, category: str | None, page: PageRequest
    ) -> Page[Product]:
        return await self.products.search(query=query, category=category, page=page)

    async def find_by_identifier(self, scheme: IdentifierScheme, raw_value: str) -> Product:
        try:
            value = normalize_identifier(scheme, raw_value)
        except InvalidIdentifierError as exc:
            raise InvalidIdentifierValueError(str(exc)) from exc
        product = await self.products.find_by_identifier(scheme, value)
        if product is None:
            raise ProductNotFoundError
        return product

    async def add_variant(
        self, product_id: uuid.UUID, user: User, *, name: str, attributes: dict[str, Any]
    ) -> Product:
        product = await self._editable(product_id, user)
        if any(v.name.casefold() == name.casefold() for v in product.variants):
            raise VariantAlreadyExistsError
        self.session.add(ProductVariant(product_id=product.id, name=name, attributes=attributes))
        await self._commit_or_conflict(VariantAlreadyExistsError)
        self.session.expire(product)
        return await self.get_product(product_id)

    async def add_identifier(
        self,
        product_id: uuid.UUID,
        user: User,
        *,
        scheme: IdentifierScheme,
        value: str,
        variant_id: uuid.UUID | None,
    ) -> Product:
        product = await self._editable(product_id, user)
        try:
            normalized = normalize_identifier(scheme, value)
        except InvalidIdentifierError as exc:
            raise InvalidIdentifierValueError(str(exc)) from exc
        if (
            variant_id is not None
            and await self.products.get_variant(product.id, variant_id) is None
        ):
            raise VariantNotFoundError
        if await self.products.identifier_exists(scheme, normalized):
            raise IdentifierAlreadyExistsError
        self.session.add(
            ProductIdentifier(
                product_id=product.id, variant_id=variant_id, scheme=scheme, value=normalized
            )
        )
        await self._commit_or_conflict(IdentifierAlreadyExistsError)
        self.session.expire(product)
        return await self.get_product(product_id)

    async def upsert_specifications(
        self, product_id: uuid.UUID, user: User, specs: list[SpecInput]
    ) -> Product:
        """Insert or replace each (variant, key) value. All-or-nothing."""
        product = await self._editable(product_id, user)
        for spec in specs:
            if (
                spec.variant_id is not None
                and await self.products.get_variant(product.id, spec.variant_id) is None
            ):
                raise VariantNotFoundError
            existing = await self.products.get_specification(product.id, spec.variant_id, spec.key)
            if existing is None:
                self.session.add(
                    ProductSpecification(
                        product_id=product.id,
                        variant_id=spec.variant_id,
                        key=spec.key,
                        value_number=spec.value_number,
                        value_text=spec.value_text,
                        unit=spec.unit,
                    )
                )
            else:
                existing.value_number = spec.value_number
                existing.value_text = spec.value_text
                existing.unit = spec.unit
        await self.session.commit()
        self.session.expire(product)
        return await self.get_product(product_id)


class WorkspaceProductService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.workspaces = WorkspaceService(session)
        self.products = ProductRepository(session)
        self.items = WorkspaceProductRepository(session)

    async def add(
        self,
        workspace_id: uuid.UUID,
        user: User,
        *,
        product_id: uuid.UUID,
        variant_id: uuid.UUID | None,
        notes: str | None,
    ) -> WorkspaceProduct:
        await self.workspaces.authorize(workspace_id, user, WorkspaceRole.EDITOR)
        if await self.products.get(product_id) is None:
            raise ProductNotFoundError
        if (
            variant_id is not None
            and await self.products.get_variant(product_id, variant_id) is None
        ):
            raise VariantNotFoundError
        if await self.items.get_in_workspace(workspace_id, product_id) is not None:
            raise ProductAlreadyInWorkspaceError
        item = WorkspaceProduct(
            workspace_id=workspace_id,
            product_id=product_id,
            variant_id=variant_id,
            added_by_id=user.id,
            notes=notes,
        )
        self.session.add(item)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ProductAlreadyInWorkspaceError from exc
        found = await self.items.get_in_workspace(workspace_id, product_id)
        assert found is not None  # noqa: S101 - just committed
        return found

    async def list(self, workspace_id: uuid.UUID, user: User) -> list[WorkspaceProduct]:
        await self.workspaces.authorize(workspace_id, user)
        return await self.items.list_for_workspace(workspace_id)

    async def remove(self, workspace_id: uuid.UUID, user: User, product_id: uuid.UUID) -> None:
        await self.workspaces.authorize(workspace_id, user, WorkspaceRole.EDITOR)
        item = await self.items.get_in_workspace(workspace_id, product_id)
        if item is None:
            raise WorkspaceProductNotFoundError
        await self.session.delete(item)
        await self.session.commit()
