"""Comparison-workspace endpoints. Every route requires authentication."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, SessionDep
from app.models.identity import ComparisonWorkspace, WorkspaceMember
from app.repositories.base import MAX_PAGE_SIZE, PageRequest
from app.schemas.workspaces import (
    PageMeta,
    WorkspaceCreate,
    WorkspaceListResponse,
    WorkspaceMemberResponse,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from app.services.workspaces import WorkspaceService

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "Workspace does not exist or you are not a member"}
}


def _to_response(workspace: ComparisonWorkspace, member: WorkspaceMember) -> WorkspaceResponse:
    return WorkspaceResponse(
        id=workspace.id,
        organization_id=workspace.organization_id,
        name=workspace.name,
        description=workspace.description,
        created_by_id=workspace.created_by_id,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
        my_role=member.role,
    )


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate, user: CurrentUser, session: SessionDep
) -> WorkspaceResponse:
    workspace, member = await WorkspaceService(session).create(
        user, name=body.name, description=body.description
    )
    return _to_response(workspace, member)


@router.get("", response_model=WorkspaceListResponse, summary="Workspaces you are a member of")
async def list_workspaces(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WorkspaceListResponse:
    page = await WorkspaceService(session).list_for_user(user, PageRequest(limit, offset))
    return WorkspaceListResponse(
        items=[_to_response(ws, member) for ws, member in page.items],
        page=PageMeta(total=page.total, limit=page.limit, offset=page.offset),
    )


@router.get("/{workspace_id}", response_model=WorkspaceResponse, responses=_NOT_FOUND)
async def get_workspace(
    workspace_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> WorkspaceResponse:
    member = await WorkspaceService(session).get(workspace_id, user)
    return _to_response(member.workspace, member)


@router.patch(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
    summary="Update workspace details (OWNER or EDITOR)",
    responses={**_NOT_FOUND, 403: {"description": "Your role cannot edit this workspace"}},
)
async def update_workspace(
    workspace_id: uuid.UUID, body: WorkspaceUpdate, user: CurrentUser, session: SessionDep
) -> WorkspaceResponse:
    changes = body.model_dump(exclude_unset=True)
    member = await WorkspaceService(session).update(workspace_id, user, changes=changes)
    return _to_response(member.workspace, member)


@router.get(
    "/{workspace_id}/members", response_model=list[WorkspaceMemberResponse], responses=_NOT_FOUND
)
async def list_members(
    workspace_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> list[WorkspaceMemberResponse]:
    members = await WorkspaceService(session).list_members(workspace_id, user)
    return [
        WorkspaceMemberResponse(
            user_id=m.user_id,
            display_name=m.user.display_name,
            email=m.user.email,
            role=m.role,
            joined_at=m.created_at,
        )
        for m in members
    ]
