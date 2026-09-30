"""Evidence packs and citation validation."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, EmbedderDep, SessionDep
from app.domain.citations import CitationReport
from app.models.evidence import EvidencePack
from app.repositories.base import MAX_PAGE_SIZE
from app.schemas.evidence import (
    CreateEvidencePackRequest,
    EvidenceItemResponse,
    EvidencePackListResponse,
    EvidencePackResponse,
    EvidencePackSummary,
    ValidateCitationsRequest,
)
from app.schemas.workspaces import PageMeta
from app.services.evidence import EvidenceService
from app.services.search import SearchFilters

router = APIRouter(prefix="/workspaces/{workspace_id}/evidence-packs", tags=["evidence"])


def _pack(pack: EvidencePack) -> EvidencePackResponse:
    item_fields = [name for name in EvidenceItemResponse.model_fields if name != "marker"]
    return EvidencePackResponse(
        **EvidencePackSummary.model_validate(pack).model_dump(),
        items=[
            EvidenceItemResponse(
                marker=f"E{item.position}", **{name: getattr(item, name) for name in item_fields}
            )
            for item in pack.items
        ],
    )


@router.post(
    "",
    response_model=EvidencePackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Search and freeze the results as citable evidence (E1..En)",
)
async def create_evidence_pack(
    workspace_id: uuid.UUID,
    body: CreateEvidencePackRequest,
    user: CurrentUser,
    session: SessionDep,
    embedder: EmbedderDep,
) -> EvidencePackResponse:
    pack = await EvidenceService(session, embedder).create(
        workspace_id,
        user,
        body.query,
        mode=body.mode,
        limit=body.limit,
        filters=SearchFilters(
            product_ids=body.product_ids,
            source_ids=body.source_ids,
            authorities=[a.value for a in body.authorities],
            source_types=[t.value for t in body.source_types],
        ),
    )
    return _pack(pack)


@router.get("", response_model=EvidencePackListResponse)
async def list_evidence_packs(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    embedder: EmbedderDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EvidencePackListResponse:
    packs, total = await EvidenceService(session, embedder).list(
        workspace_id, user, limit=limit, offset=offset
    )
    return EvidencePackListResponse(
        items=[EvidencePackSummary.model_validate(p) for p in packs],
        page=PageMeta(total=total, limit=limit, offset=offset),
    )


@router.get("/{pack_id}", response_model=EvidencePackResponse)
async def get_evidence_pack(
    workspace_id: uuid.UUID,
    pack_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    embedder: EmbedderDep,
) -> EvidencePackResponse:
    return _pack(await EvidenceService(session, embedder).get(workspace_id, user, pack_id))


@router.post(
    "/{pack_id}/validate",
    response_model=CitationReport,
    summary="Check that text cites this pack correctly ([E1] markers, numbers, quotes)",
)
async def validate_citations(
    workspace_id: uuid.UUID,
    pack_id: uuid.UUID,
    body: ValidateCitationsRequest,
    user: CurrentUser,
    session: SessionDep,
    embedder: EmbedderDep,
) -> CitationReport:
    return await EvidenceService(session, embedder).validate(workspace_id, user, pack_id, body.text)
