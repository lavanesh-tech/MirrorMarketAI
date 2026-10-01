"""Comments, votes and presence. Every change is also published as a realtime event."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, EventsDep, PresenceDep, SessionDep
from app.repositories.base import MAX_PAGE_SIZE
from app.schemas.collaboration import (
    CommentBody,
    CommentCreate,
    CommentListResponse,
    CommentResponse,
    PresenceResponse,
    VoteIn,
    VoteTally,
    VoteTallyList,
)
from app.schemas.workspaces import PageMeta
from app.services.collaboration import (
    EVENT_COMMENT_CREATED,
    EVENT_COMMENT_DELETED,
    EVENT_COMMENT_UPDATED,
    EVENT_VOTE_CHANGED,
    CollaborationService,
    comment_response,
)
from app.services.workspaces import WorkspaceService

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["collaboration"])


@router.post(
    "/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Comment on the workspace or on one of its products (MEMBER+)",
)
async def add_comment(
    workspace_id: uuid.UUID,
    body: CommentCreate,
    user: CurrentUser,
    session: SessionDep,
    events: EventsDep,
) -> CommentResponse:
    comment = await CollaborationService(session).add_comment(
        workspace_id, user, body=body.body, product_id=body.product_id, parent_id=body.parent_id
    )
    response = comment_response(comment)
    await events.publish(
        workspace_id, EVENT_COMMENT_CREATED, response.model_dump(mode="json"), user.id
    )
    return response


@router.get(
    "/comments",
    response_model=CommentListResponse,
    summary="Comments, oldest first (deleted ones are kept as placeholders)",
)
async def list_comments(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    product_id: uuid.UUID | None = None,
    workspace_level_only: Annotated[
        bool, Query(description="Only comments that are not about a product")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CommentListResponse:
    comments, total = await CollaborationService(session).list_comments(
        workspace_id,
        user,
        product_id=product_id,
        workspace_level_only=workspace_level_only,
        limit=limit,
        offset=offset,
    )
    return CommentListResponse(
        items=[comment_response(c) for c in comments],
        page=PageMeta(total=total, limit=limit, offset=offset),
    )


@router.patch(
    "/comments/{comment_id}", response_model=CommentResponse, summary="Edit your own comment"
)
async def edit_comment(
    workspace_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: CommentBody,
    user: CurrentUser,
    session: SessionDep,
    events: EventsDep,
) -> CommentResponse:
    comment = await CollaborationService(session).edit_comment(
        workspace_id, user, comment_id, body.body
    )
    response = comment_response(comment)
    await events.publish(
        workspace_id, EVENT_COMMENT_UPDATED, response.model_dump(mode="json"), user.id
    )
    return response


@router.delete(
    "/comments/{comment_id}",
    response_model=CommentResponse,
    summary="Delete a comment (author or workspace OWNER); the text is erased",
)
async def delete_comment(
    workspace_id: uuid.UUID,
    comment_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    events: EventsDep,
) -> CommentResponse:
    comment = await CollaborationService(session).delete_comment(workspace_id, user, comment_id)
    response = comment_response(comment)
    await events.publish(
        workspace_id,
        EVENT_COMMENT_DELETED,
        {"id": str(comment.id), "product_id": comment.product_id, "parent_id": comment.parent_id},
        user.id,
    )
    return response


@router.put(
    "/products/{product_id}/vote",
    response_model=VoteTally,
    summary="Vote a workspace product up (1) or down (-1), or remove your vote (0) (MEMBER+)",
)
async def vote(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    body: VoteIn,
    user: CurrentUser,
    session: SessionDep,
    events: EventsDep,
) -> VoteTally:
    tally = await CollaborationService(session).vote(workspace_id, user, product_id, body.value)
    # `my_vote` is per reader, so the broadcast carries only the shared totals.
    await events.publish(
        workspace_id,
        EVENT_VOTE_CHANGED,
        tally.model_dump(mode="json", exclude={"my_vote"}),
        user.id,
    )
    return tally


@router.get("/votes", response_model=VoteTallyList, summary="Vote totals per product + my vote")
async def list_votes(
    workspace_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> VoteTallyList:
    return VoteTallyList(items=await CollaborationService(session).tallies(workspace_id, user))


@router.get("/presence", response_model=PresenceResponse, summary="Members currently connected")
async def presence(
    workspace_id: uuid.UUID, user: CurrentUser, session: SessionDep, online: PresenceDep
) -> PresenceResponse:
    await WorkspaceService(session).authorize(workspace_id, user)
    users = await online.users(workspace_id)
    return PresenceResponse(workspace_id=workspace_id, user_ids=[uuid.UUID(u) for u in users])
