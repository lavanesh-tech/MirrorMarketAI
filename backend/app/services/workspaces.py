"""Comparison-workspace use-cases with role-based authorization.

Authorization rule of thumb:
- not a member            -> 404 (don't reveal that the workspace exists: prevents IDOR probing)
- member, role too low    -> 403
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError, WorkspaceNotFoundError
from app.domain.roles import OrganizationRole, WorkspaceRole, has_at_least
from app.models.identity import ComparisonWorkspace, OrganizationMember, User, WorkspaceMember
from app.repositories.base import Page, PageRequest
from app.repositories.identity import WorkspaceRepository
from app.security import audit_trail as audit


class WorkspaceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.workspaces = WorkspaceRepository(session)

    async def authorize(
        self, workspace_id: uuid.UUID, user: User, minimum: WorkspaceRole = WorkspaceRole.VIEWER
    ) -> WorkspaceMember:
        membership = await self.workspaces.get_membership(workspace_id, user.id)
        if membership is None:
            raise WorkspaceNotFoundError
        if not has_at_least(membership.role, minimum):
            raise PermissionDeniedError
        return membership

    async def _personal_organization_id(self, user: User) -> uuid.UUID:
        org_id = await self.session.scalar(
            select(OrganizationMember.organization_id)
            .where(
                OrganizationMember.user_id == user.id,
                OrganizationMember.role == OrganizationRole.OWNER,
            )
            .order_by(OrganizationMember.created_at)
            .limit(1)
        )
        if org_id is None:  # every registered user has one; defensive
            raise PermissionDeniedError("You do not belong to an organization.")
        return org_id

    async def create(
        self, user: User, *, name: str, description: str | None
    ) -> tuple[ComparisonWorkspace, WorkspaceMember]:
        workspace = await self.workspaces.add(
            ComparisonWorkspace(
                organization_id=await self._personal_organization_id(user),
                created_by_id=user.id,
                name=name,
                description=description,
            )
        )
        member = await self.workspaces.add_member(
            WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OWNER)
        )
        audit.record(
            self.session, audit.WORKSPACE_CREATED, actor_id=user.id, workspace_id=workspace.id
        )
        await self.session.commit()
        return workspace, member

    async def list_for_user(
        self, user: User, page: PageRequest
    ) -> Page[tuple[ComparisonWorkspace, WorkspaceMember]]:
        return await self.workspaces.list_for_user(user.id, page)

    async def get(self, workspace_id: uuid.UUID, user: User) -> WorkspaceMember:
        return await self.authorize(workspace_id, user)

    async def update(
        self, workspace_id: uuid.UUID, user: User, *, changes: dict[str, str | None]
    ) -> WorkspaceMember:
        membership = await self.authorize(workspace_id, user, WorkspaceRole.EDITOR)
        workspace = membership.workspace
        for field, value in changes.items():
            setattr(workspace, field, value)
        audit.record(
            self.session,
            audit.WORKSPACE_UPDATED,
            actor_id=user.id,
            workspace_id=workspace_id,
            details={"fields": sorted(changes)},
        )
        await self.session.commit()
        await self.session.refresh(workspace)
        return membership

    async def list_members(self, workspace_id: uuid.UUID, user: User) -> list[WorkspaceMember]:
        await self.authorize(workspace_id, user)
        return await self.workspaces.list_members(workspace_id)

    async def add_member(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID, role: WorkspaceRole
    ) -> WorkspaceMember:
        """Internal: used by the invitation flow (later phase) and tests."""
        member = await self.workspaces.add_member(
            WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role)
        )
        await self.session.commit()
        return member
