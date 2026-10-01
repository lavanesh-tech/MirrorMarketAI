"""Reading the audit trail (writing is `app.security.audit_trail.record`)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.roles import WorkspaceRole
from app.models.identity import User
from app.models.security import AuditLog
from app.services.workspaces import WorkspaceService


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _page(self, where: Any, limit: int, offset: int) -> tuple[list[AuditLog], int]:
        total = await self.session.scalar(select(func.count()).select_from(AuditLog).where(where))
        rows = await self.session.scalars(
            select(AuditLog)
            .where(where)
            .order_by(AuditLog.occurred_at.desc(), AuditLog.id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows), total or 0

    async def for_user(self, user: User, *, limit: int, offset: int) -> tuple[list[AuditLog], int]:
        """A user's own security events (logins, refreshes, password changes)."""
        return await self._page(AuditLog.actor_id == user.id, limit, offset)

    async def for_workspace(
        self, workspace_id: uuid.UUID, user: User, *, limit: int, offset: int
    ) -> tuple[list[AuditLog], int]:
        await WorkspaceService(self.session).authorize(workspace_id, user, WorkspaceRole.OWNER)
        return await self._page(AuditLog.workspace_id == workspace_id, limit, offset)
