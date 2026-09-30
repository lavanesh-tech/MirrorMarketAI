"""Repository base-class tests against real PostgreSQL.

Uses a test-only model on its own metadata so it never leaks into the
application's schema (and never trips the schema-drift test).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import MetaData, String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.base import NAMING_CONVENTION, TimestampMixin, UUIDPrimaryKeyMixin
from app.repositories.base import PageRequest, Repository

pytestmark = pytest.mark.db


class _TestBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Gadget(UUIDPrimaryKeyMixin, TimestampMixin, _TestBase):
    __tablename__ = "test_gadget"
    __table_args__ = (UniqueConstraint("name"),)

    name: Mapped[str] = mapped_column(String(100))


class GadgetRepository(Repository[Gadget]):
    model = Gadget


@pytest.fixture
async def repo(connection: AsyncConnection, db_session: AsyncSession) -> GadgetRepository:
    # DDL is transactional in PostgreSQL: the table disappears with the rollback.
    await connection.run_sync(_TestBase.metadata.create_all)
    return GadgetRepository(db_session)


async def test_add_assigns_uuid_and_server_timestamps(repo: GadgetRepository) -> None:
    gadget = await repo.add(Gadget(name="laptop"))

    assert isinstance(gadget.id, uuid.UUID)
    assert gadget.created_at.tzinfo is not None
    assert abs((datetime.now(UTC) - gadget.created_at).total_seconds()) < 60
    assert gadget.updated_at == gadget.created_at


async def test_get_returns_entity_or_none(repo: GadgetRepository) -> None:
    gadget = await repo.add(Gadget(name="phone"))
    assert (await repo.get(gadget.id)) is gadget
    assert await repo.get(uuid.uuid4()) is None


async def test_delete_removes_entity(repo: GadgetRepository) -> None:
    gadget = await repo.add(Gadget(name="tablet"))
    await repo.delete(gadget)
    assert await repo.get(gadget.id) is None


async def test_list_paginates_with_total(repo: GadgetRepository) -> None:
    for index in range(5):
        await repo.add(Gadget(name=f"item-{index}"))

    first = await repo.list(PageRequest(limit=2, offset=0))
    second = await repo.list(PageRequest(limit=2, offset=2))
    last = await repo.list(PageRequest(limit=2, offset=4))

    assert first.total == second.total == last.total == 5
    assert [len(p.items) for p in (first, second, last)] == [2, 2, 1]
    seen = [g.id for page in (first, second, last) for g in page.items]
    assert len(set(seen)) == 5  # no duplicates or gaps across pages


async def test_list_empty_table(repo: GadgetRepository) -> None:
    page = await repo.list()
    assert page.total == 0
    assert page.items == []


async def test_unique_constraint_is_enforced_by_database(
    repo: GadgetRepository, db_session: AsyncSession
) -> None:
    await repo.add(Gadget(name="duplicate"))
    with pytest.raises(IntegrityError, match="uq_test_gadget_name"):
        await repo.add(Gadget(name="duplicate"))
    await db_session.rollback()


async def test_failed_unit_of_work_persists_nothing(
    repo: GadgetRepository, db_session: AsyncSession
) -> None:
    """Service-style transaction: an error mid-way must roll back every write."""

    async def unit_of_work() -> None:
        async with db_session.begin_nested():
            await repo.add(Gadget(name="first"))
            await repo.add(Gadget(name="second"))
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await unit_of_work()

    remaining = await db_session.scalars(select(Gadget))
    assert list(remaining) == []


async def test_committed_unit_of_work_is_visible(
    repo: GadgetRepository, db_session: AsyncSession
) -> None:
    await repo.add(Gadget(name="kept"))
    await db_session.commit()  # becomes a SAVEPOINT release inside the test transaction

    names = await db_session.scalars(select(Gadget.name))
    assert list(names) == ["kept"]
