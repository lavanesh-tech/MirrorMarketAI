"""Products inside a comparison workspace (tenant-scoped)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, SessionDep
from app.models.catalog import WorkspaceProduct
from app.schemas.catalog import ProductSummary, WorkspaceProductAdd, WorkspaceProductResponse
from app.services.catalog import WorkspaceProductService

router = APIRouter(prefix="/workspaces/{workspace_id}/products", tags=["workspace products"])


def _to_response(item: WorkspaceProduct) -> WorkspaceProductResponse:
    return WorkspaceProductResponse(
        product=ProductSummary.model_validate(item.product),
        variant_id=item.variant_id,
        notes=item.notes,
        added_by_id=item.added_by_id,
        added_at=item.created_at,
    )


@router.post(
    "",
    response_model=WorkspaceProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a catalog product to the workspace (OWNER or EDITOR)",
)
async def add_product(
    workspace_id: uuid.UUID, body: WorkspaceProductAdd, user: CurrentUser, session: SessionDep
) -> WorkspaceProductResponse:
    item = await WorkspaceProductService(session).add(
        workspace_id, user, product_id=body.product_id, variant_id=body.variant_id, notes=body.notes
    )
    return _to_response(item)


@router.get("", response_model=list[WorkspaceProductResponse])
async def list_products(
    workspace_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> list[WorkspaceProductResponse]:
    items = await WorkspaceProductService(session).list(workspace_id, user)
    return [_to_response(item) for item in items]


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a product from the workspace (OWNER or EDITOR)",
)
async def remove_product(
    workspace_id: uuid.UUID, product_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Response:
    await WorkspaceProductService(session).remove(workspace_id, user, product_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
