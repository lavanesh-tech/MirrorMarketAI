"""Generic async repository.

Repositories are the only layer that builds SQL. They receive an `AsyncSession`
from the caller and never commit: the service layer owns the transaction, so
several repository calls can succeed or fail together.

Domain repositories subclass this and add intention-revealing queries, e.g.
`WorkspaceRepository.list_for_member(user_id, page)`. Tenant/workspace filters
belong in those subclass methods (Phase 3+), never in route handlers.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class PageRequest:
    """Offset pagination parameters, validated so callers can't request unbounded pages."""

    limit: int = 20
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        if self.offset < 0:
            raise ValueError("offset must be >= 0")


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: Sequence[T]
    total: int
    limit: int
    offset: int


class Repository[ModelT: DeclarativeBase]:
    """CRUD primitives for a single model with a UUID `id` primary key."""

    model: ClassVar[type[Any]]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        entity: ModelT | None = await self.session.get(self.model, entity_id)
        return entity

    async def add(self, entity: ModelT) -> ModelT:
        """Stage an INSERT and flush so DB defaults/constraints apply immediately."""
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        await self.session.delete(entity)
        await self.session.flush()

    async def list(self, page: PageRequest | None = None) -> Page[ModelT]:
        page = page or PageRequest()
        total = await self.session.scalar(select(func.count()).select_from(self.model)) or 0
        rows: list[ModelT] = list(
            await self.session.scalars(
                select(self.model)
                .order_by(self.model.id)  # stable order is required for correct pagination
                .limit(page.limit)
                .offset(page.offset)
            )
        )
        return Page(items=rows, total=total, limit=page.limit, offset=page.offset)
