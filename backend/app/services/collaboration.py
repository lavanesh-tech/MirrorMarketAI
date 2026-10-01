"""Comments and votes inside a workspace (MEMBER and above may write)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    CommentNotFoundError,
    InvalidCommentParentError,
    PermissionDeniedError,
    WorkspaceProductNotFoundError,
)
from app.domain.roles import WorkspaceRole, has_at_least
from app.events.envelope import COMMENT_CREATED, COMMENT_DELETED, VOTE_CHANGED, new_event
from app.models.catalog import WorkspaceProduct
from app.models.collaboration import ProductVote, WorkspaceComment
from app.models.events import WorkspaceActivity
from app.models.identity import User
from app.schemas.collaboration import CommentResponse, VoteTally
from app.security import audit_trail as audit
from app.services.workspaces import WorkspaceService

EVENT_COMMENT_CREATED = "comment.created"
EVENT_COMMENT_UPDATED = "comment.updated"
EVENT_COMMENT_DELETED = "comment.deleted"
EVENT_VOTE_CHANGED = "vote.changed"
EXCERPT_CHARS = 120


def comment_response(comment: WorkspaceComment) -> CommentResponse:
    deleted = comment.deleted_at is not None
    return CommentResponse(
        id=comment.id,
        workspace_id=comment.workspace_id,
        product_id=comment.product_id,
        parent_id=comment.parent_id,
        author_id=comment.author_id,
        author_name=comment.author.display_name,
        body=None if deleted else comment.body,
        deleted=deleted,
        edited_at=comment.edited_at,
        created_at=comment.created_at,
    )


class CollaborationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.workspaces = WorkspaceService(session)

    async def _require_workspace_product(
        self, workspace_id: uuid.UUID, product_id: uuid.UUID
    ) -> None:
        found = await self.session.scalar(
            select(WorkspaceProduct.id).where(
                WorkspaceProduct.workspace_id == workspace_id,
                WorkspaceProduct.product_id == product_id,
            )
        )
        if found is None:
            raise WorkspaceProductNotFoundError

    async def _comment(self, workspace_id: uuid.UUID, comment_id: uuid.UUID) -> WorkspaceComment:
        # Filtering by workspace_id too: a comment id from another tenant is "not found".
        comment: WorkspaceComment | None = await self.session.scalar(
            select(WorkspaceComment).where(
                WorkspaceComment.id == comment_id, WorkspaceComment.workspace_id == workspace_id
            )
        )
        if comment is None:
            raise CommentNotFoundError
        return comment

    async def add_comment(
        self,
        workspace_id: uuid.UUID,
        user: User,
        *,
        body: str,
        product_id: uuid.UUID | None,
        parent_id: uuid.UUID | None,
    ) -> WorkspaceComment:
        await self.workspaces.authorize(workspace_id, user, WorkspaceRole.MEMBER)
        if product_id is not None:
            await self._require_workspace_product(workspace_id, product_id)
        if parent_id is not None:
            parent = await self._comment(workspace_id, parent_id)
            if (
                parent.parent_id is not None
                or parent.deleted_at is not None
                or parent.product_id != product_id
            ):
                raise InvalidCommentParentError
        comment = WorkspaceComment(
            id=uuid.uuid4(),  # needed before flush: the event below refers to it
            workspace_id=workspace_id,
            product_id=product_id,
            parent_id=parent_id,
            author_id=user.id,
            body=body,
        )
        self.session.add(comment)
        # Same transaction as the comment: the event exists iff the comment does.
        self.session.add(
            new_event(
                COMMENT_CREATED,
                {
                    "comment_id": str(comment.id),
                    "parent_id": str(parent_id) if parent_id else None,
                    "excerpt": body[:EXCERPT_CHARS],
                },
                workspace_id=workspace_id,
                actor_id=user.id,
                product_id=product_id,
            )
        )
        await self.session.commit()
        await self.session.refresh(comment)
        return comment

    async def list_comments(
        self,
        workspace_id: uuid.UUID,
        user: User,
        *,
        product_id: uuid.UUID | None,
        workspace_level_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[WorkspaceComment], int]:
        await self.workspaces.authorize(workspace_id, user)
        where = [WorkspaceComment.workspace_id == workspace_id]
        if product_id is not None:
            where.append(WorkspaceComment.product_id == product_id)
        elif workspace_level_only:
            where.append(WorkspaceComment.product_id.is_(None))
        total = await self.session.scalar(
            select(func.count()).select_from(WorkspaceComment).where(*where)
        )
        rows = await self.session.scalars(
            select(WorkspaceComment)
            .where(*where)
            .order_by(WorkspaceComment.created_at, WorkspaceComment.id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows), total or 0

    async def edit_comment(
        self, workspace_id: uuid.UUID, user: User, comment_id: uuid.UUID, body: str
    ) -> WorkspaceComment:
        await self.workspaces.authorize(workspace_id, user, WorkspaceRole.MEMBER)
        comment = await self._comment(workspace_id, comment_id)
        if comment.deleted_at is not None:
            raise CommentNotFoundError
        if comment.author_id != user.id:
            raise PermissionDeniedError("Only the author can edit a comment.")
        comment.body = body
        comment.edited_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(comment)
        return comment

    async def delete_comment(
        self, workspace_id: uuid.UUID, user: User, comment_id: uuid.UUID
    ) -> WorkspaceComment:
        """Soft delete by the author or a workspace OWNER; idempotent."""
        membership = await self.workspaces.authorize(workspace_id, user, WorkspaceRole.MEMBER)
        comment = await self._comment(workspace_id, comment_id)
        is_owner = has_at_least(membership.role, WorkspaceRole.OWNER)
        if comment.author_id != user.id and not is_owner:
            raise PermissionDeniedError("Only the author or a workspace owner can delete this.")
        if comment.deleted_at is None:
            comment.deleted_at = datetime.now(UTC)
            comment.body = "[deleted]"  # the text itself is erased, not just hidden
            if comment.author_id != user.id:  # an OWNER removed someone else's comment
                audit.record(
                    self.session,
                    audit.COMMENT_MODERATED,
                    actor_id=user.id,
                    workspace_id=workspace_id,
                    target_type="comment",
                    target_id=comment.id,
                    details={"author_id": str(comment.author_id)},
                )
            self.session.add(
                new_event(
                    COMMENT_DELETED,
                    {"comment_id": str(comment.id)},
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    product_id=comment.product_id,
                )
            )
            await self.session.commit()
            await self.session.refresh(comment)
        return comment

    async def _tallies(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID, product_id: uuid.UUID | None = None
    ) -> list[VoteTally]:
        where = [ProductVote.workspace_id == workspace_id]
        if product_id is not None:
            where.append(ProductVote.product_id == product_id)
        rows = await self.session.execute(
            select(
                ProductVote.product_id,
                func.count().filter(ProductVote.value == 1),
                func.count().filter(ProductVote.value == -1),
                func.coalesce(
                    func.max(case((ProductVote.user_id == user_id, ProductVote.value))), 0
                ),
            )
            .where(*where)
            .group_by(ProductVote.product_id)
            .order_by(ProductVote.product_id)
        )
        return [
            VoteTally(product_id=pid, up=up, down=down, score=up - down, my_vote=mine)
            for pid, up, down, mine in rows
        ]

    async def vote(
        self, workspace_id: uuid.UUID, user: User, product_id: uuid.UUID, value: int
    ) -> VoteTally:
        await self.workspaces.authorize(workspace_id, user, WorkspaceRole.MEMBER)
        await self._require_workspace_product(workspace_id, product_id)
        if value == 0:
            await self.session.execute(
                delete(ProductVote).where(
                    ProductVote.workspace_id == workspace_id,
                    ProductVote.product_id == product_id,
                    ProductVote.user_id == user.id,
                )
            )
        else:
            # One atomic upsert: two tabs voting at once cannot create two rows.
            stmt = insert(ProductVote).values(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                product_id=product_id,
                user_id=user.id,
                value=value,
            )
            await self.session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["workspace_id", "product_id", "user_id"],
                    set_={"value": stmt.excluded.value, "updated_at": func.now()},
                )
            )
        self.session.add(
            new_event(
                VOTE_CHANGED,
                {"value": value},
                workspace_id=workspace_id,
                actor_id=user.id,
                product_id=product_id,
            )
        )
        await self.session.commit()
        tallies = await self._tallies(workspace_id, user.id, product_id)
        return (
            tallies[0]
            if tallies
            else VoteTally(product_id=product_id, up=0, down=0, score=0, my_vote=0)
        )

    async def tallies(self, workspace_id: uuid.UUID, user: User) -> list[VoteTally]:
        await self.workspaces.authorize(workspace_id, user)
        return await self._tallies(workspace_id, user.id)

    async def activity(
        self, workspace_id: uuid.UUID, user: User, *, limit: int, offset: int
    ) -> tuple[list[WorkspaceActivity], int]:
        """Newest first. Built asynchronously from events, so it lags writes slightly."""
        await self.workspaces.authorize(workspace_id, user)
        where = WorkspaceActivity.workspace_id == workspace_id
        total = await self.session.scalar(
            select(func.count()).select_from(WorkspaceActivity).where(where)
        )
        rows = await self.session.scalars(
            select(WorkspaceActivity)
            .where(where)
            .order_by(WorkspaceActivity.occurred_at.desc(), WorkspaceActivity.id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows), total or 0
