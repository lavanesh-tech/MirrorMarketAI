"""Product catalog endpoints (authenticated)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, SessionDep
from app.domain.products import IdentifierScheme
from app.repositories.base import MAX_PAGE_SIZE, PageRequest
from app.schemas.catalog import (
    Category,
    IdentifierCreate,
    ProductCreate,
    ProductDetail,
    ProductListResponse,
    ProductSummary,
    SpecificationsUpsert,
    VariantCreate,
)
from app.schemas.workspaces import PageMeta
from app.services.catalog import CatalogService, SpecInput

router = APIRouter(prefix="/products", tags=["products"])


@router.post(
    "",
    response_model=ProductDetail,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Same brand + name already exists"}},
)
async def create_product(
    body: ProductCreate, user: CurrentUser, session: SessionDep
) -> ProductDetail:
    product = await CatalogService(session).create_product(
        user,
        brand=body.brand,
        name=body.name,
        category=body.category,
        description=body.description,
    )
    return ProductDetail.model_validate(product)


@router.get("", response_model=ProductListResponse, summary="Search the catalog")
async def search_products(
    _: CurrentUser,
    session: SessionDep,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    category: Category | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductListResponse:
    page = await CatalogService(session).search(
        query=q, category=category, page=PageRequest(limit, offset)
    )
    return ProductListResponse(
        items=[ProductSummary.model_validate(p) for p in page.items],
        page=PageMeta(total=page.total, limit=page.limit, offset=page.offset),
    )


@router.get(
    "/by-identifier",
    response_model=ProductDetail,
    summary="Find a product by GTIN/UPC/EAN, MPN, ASIN or SKU",
)
async def get_by_identifier(
    _: CurrentUser,
    session: SessionDep,
    scheme: IdentifierScheme,
    value: Annotated[str, Query(min_length=1, max_length=64)],
) -> ProductDetail:
    product = await CatalogService(session).find_by_identifier(scheme, value)
    return ProductDetail.model_validate(product)


@router.get("/{product_id}", response_model=ProductDetail)
async def get_product(product_id: uuid.UUID, _: CurrentUser, session: SessionDep) -> ProductDetail:
    return ProductDetail.model_validate(await CatalogService(session).get_product(product_id))


@router.post(
    "/{product_id}/variants", response_model=ProductDetail, status_code=status.HTTP_201_CREATED
)
async def add_variant(
    product_id: uuid.UUID, body: VariantCreate, user: CurrentUser, session: SessionDep
) -> ProductDetail:
    product = await CatalogService(session).add_variant(
        product_id, user, name=body.name, attributes=dict(body.attributes)
    )
    return ProductDetail.model_validate(product)


@router.post(
    "/{product_id}/identifiers", response_model=ProductDetail, status_code=status.HTTP_201_CREATED
)
async def add_identifier(
    product_id: uuid.UUID, body: IdentifierCreate, user: CurrentUser, session: SessionDep
) -> ProductDetail:
    product = await CatalogService(session).add_identifier(
        product_id, user, scheme=body.scheme, value=body.value, variant_id=body.variant_id
    )
    return ProductDetail.model_validate(product)


@router.put(
    "/{product_id}/specifications",
    response_model=ProductDetail,
    summary="Insert or replace specification values (all-or-nothing)",
)
async def upsert_specifications(
    product_id: uuid.UUID, body: SpecificationsUpsert, user: CurrentUser, session: SessionDep
) -> ProductDetail:
    specs = [
        SpecInput(
            key=s.key,
            value_number=s.value_number,
            value_text=s.value_text,
            unit=s.unit,
            variant_id=s.variant_id,
        )
        for s in body.specifications
    ]
    product = await CatalogService(session).upsert_specifications(product_id, user, specs)
    return ProductDetail.model_validate(product)
