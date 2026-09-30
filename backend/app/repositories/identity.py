"""Repositories for users, organizations and workspaces.

Workspace queries always join through `workspace_members` for the requesting
user. There is intentionally no "get any workspace by id" method: tenant
isolation is enforced by the only queries that exist.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.identity import (
    ComparisonWorkspace,
    Organization,
    OrganizationMember,
    User,
    WorkspaceMember,
)
from app.repositories.base import Page, PageRequest, Repository


class UserRepository(Repository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        user: User | None = await self.session.scalar(
            select(User).where(func.lower(User.email) == email.lower())
        )
        return user


class OrganizationRepository(Repository[Organization]):
    model = Organization

    async def add_member(self, member: OrganizationMember) -> OrganizationMember:
        self.session.add(member)
        await self.session.flush()
        return member


class WorkspaceRepository(Repository[ComparisonWorkspace]):
    model = ComparisonWorkspace

    async def get_membership(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> WorkspaceMember | None:
        """The user's membership row (with its workspace), or None if not a member."""
        member: WorkspaceMember | None = await self.session.scalar(
            select(WorkspaceMember)
            .options(selectinload(WorkspaceMember.workspace))
            .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id)
        )
        return member

    async def list_for_user(
        self, user_id: uuid.UUID, page: PageRequest
    ) -> Page[tuple[ComparisonWorkspace, WorkspaceMember]]:
        base = (
            select(ComparisonWorkspace, WorkspaceMember)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == ComparisonWorkspace.id)
            .where(WorkspaceMember.user_id == user_id)
        )
        total = await self.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = await self.session.execute(
            base.order_by(ComparisonWorkspace.created_at.desc(), ComparisonWorkspace.id)
            .limit(page.limit)
            .offset(page.offset)
        )
        items = [(workspace, member) for workspace, member in rows]
        return Page(items=items, total=total, limit=page.limit, offset=page.offset)

    async def add_member(self, member: WorkspaceMember) -> WorkspaceMember:
        self.session.add(member)
        await self.session.flush()
        return member

    async def list_members(self, workspace_id: uuid.UUID) -> list[WorkspaceMember]:
        rows = await self.session.scalars(
            select(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == workspace_id)
            .order_by(WorkspaceMember.created_at, WorkspaceMember.id)
        )
        return list(rows)
