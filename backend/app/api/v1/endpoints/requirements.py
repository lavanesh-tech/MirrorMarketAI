"""Workspace purchase requirements: extract, save (versioned), history and diffs."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from app.api.deps import CurrentUser, ExtractorDep, SessionDep, SettingsDep
from app.domain.requirements import RequirementSpec
from app.models.requirements import RequirementVersion
from app.repositories.base import MAX_PAGE_SIZE
from app.schemas.requirements import (
    ExtractRequest,
    ExtractResponse,
    RequirementDiffResponse,
    RequirementsResponse,
    RequirementVersionListResponse,
    RequirementVersionResponse,
    RequirementVersionSummary,
    SaveRequirementsRequest,
)
from app.schemas.workspaces import PageMeta
from app.services.requirements import RequirementService

router = APIRouter(prefix="/workspaces/{workspace_id}/requirements", tags=["requirements"])

VersionNumber = Annotated[int, Path(ge=1)]


def _version(version: RequirementVersion) -> RequirementVersionResponse:
    return RequirementVersionResponse(
        id=version.id,
        version=version.version,
        raw_text=version.raw_text,
        spec=RequirementSpec.model_validate(version.spec),
        unparsed=list(version.unparsed),
        extractor=version.extractor,
        change_note=version.change_note,
        created_by_id=version.created_by_id,
        created_at=version.created_at,
    )


@router.post(
    "/extract",
    response_model=ExtractResponse,
    summary="Preview the structured spec for a free-text brief (nothing is saved)",
)
async def extract_requirements(
    workspace_id: uuid.UUID,
    body: ExtractRequest,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    extractor: ExtractorDep,
) -> ExtractResponse:
    outcome = await RequirementService(session, settings, extractor).preview(
        workspace_id, user, body.text
    )
    return ExtractResponse(
        spec=outcome.extraction.spec,
        unparsed=outcome.extraction.unparsed,
        extractor=outcome.extraction.extractor,
        degraded=outcome.degraded,
    )


@router.put(
    "",
    response_model=RequirementsResponse,
    responses={
        200: {"description": "Unchanged (identical content)"},
        201: {"model": RequirementsResponse, "description": "New version saved"},
    },
    summary="Save a new requirements version (optimistic locking via expected_version)",
)
async def save_requirements(
    workspace_id: uuid.UUID,
    body: SaveRequirementsRequest,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    extractor: ExtractorDep,
) -> RequirementsResponse:
    outcome = await RequirementService(session, settings, extractor).save(
        workspace_id,
        user,
        text=body.text,
        spec=body.spec,
        expected_version=body.expected_version,
        change_note=body.change_note,
    )
    response.status_code = status.HTTP_201_CREATED if outcome.created else status.HTTP_200_OK
    return RequirementsResponse(
        workspace_id=workspace_id,
        current_version=outcome.requirement.current_version,
        current=_version(outcome.version),
        degraded=outcome.degraded,
    )


@router.get("", response_model=RequirementsResponse, summary="Current requirements")
async def get_requirements(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    extractor: ExtractorDep,
) -> RequirementsResponse:
    requirement, version = await RequirementService(session, settings, extractor).current(
        workspace_id, user
    )
    return RequirementsResponse(
        workspace_id=workspace_id,
        current_version=requirement.current_version,
        current=_version(version),
    )


@router.get("/versions", response_model=RequirementVersionListResponse)
async def list_requirement_versions(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    extractor: ExtractorDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RequirementVersionListResponse:
    versions, total = await RequirementService(session, settings, extractor).list_versions(
        workspace_id, user, limit=limit, offset=offset
    )
    return RequirementVersionListResponse(
        items=[RequirementVersionSummary.model_validate(v) for v in versions],
        page=PageMeta(total=total, limit=limit, offset=offset),
    )


@router.get("/versions/{version}", response_model=RequirementVersionResponse)
async def get_requirement_version(
    workspace_id: uuid.UUID,
    version: VersionNumber,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    extractor: ExtractorDep,
) -> RequirementVersionResponse:
    found = await RequirementService(session, settings, extractor).get_version(
        workspace_id, user, version
    )
    return _version(found)


@router.get(
    "/diff",
    response_model=RequirementDiffResponse,
    summary="What changed between two versions",
)
async def diff_requirement_versions(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    extractor: ExtractorDep,
    from_version: Annotated[int, Query(alias="from", ge=1)],
    to_version: Annotated[int, Query(alias="to", ge=1)],
) -> RequirementDiffResponse:
    diff = await RequirementService(session, settings, extractor).diff(
        workspace_id, user, from_version, to_version
    )
    return RequirementDiffResponse(
        from_version=from_version,
        to_version=to_version,
        changes=diff.changes,
        criteria_added=diff.criteria_added,
        criteria_removed=diff.criteria_removed,
        criteria_changed=diff.criteria_changed,
    )
