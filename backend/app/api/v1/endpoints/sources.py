"""Evidence source endpoints: register, ingest URL, upload file, read documents."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status

from app.api.deps import CurrentUser, FetcherDep, SessionDep, SettingsDep
from app.core.errors import FileTooLargeError, SourceNotFoundError
from app.models.sources import SourceType
from app.schemas.sources import (
    DocumentMeta,
    DocumentText,
    IngestionResponse,
    SnapshotMeta,
    SourceCreate,
    SourceDetail,
    SourceResponse,
)
from app.services.sources import IngestionResult, SourceService

router = APIRouter(tags=["sources"])


def _ingestion(result: IngestionResult) -> IngestionResponse:
    return IngestionResponse(
        source=SourceResponse.model_validate(result.source),
        snapshot=SnapshotMeta.model_validate(result.snapshot),
        document=DocumentMeta.model_validate(result.document),
        unchanged=result.unchanged,
    )


@router.post(
    "/products/{product_id}/sources",
    response_model=SourceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a source (URL or placeholder) for a product",
)
async def create_source(
    product_id: uuid.UUID,
    body: SourceCreate,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> SourceResponse:
    source = await SourceService(session, settings).create_source(
        user,
        product_id=product_id,
        workspace_id=body.workspace_id,
        source_type=body.source_type,
        authority=body.authority,
        title=body.title,
        url=body.url,
    )
    return SourceResponse.model_validate(source)


@router.get(
    "/products/{product_id}/sources",
    response_model=list[SourceResponse],
    summary="Shared sources plus sources from your workspaces",
)
async def list_sources(
    product_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> list[SourceResponse]:
    sources = await SourceService(session, settings).list_for_product(product_id, user)
    return [SourceResponse.model_validate(s) for s in sources]


@router.post(
    "/products/{product_id}/sources/upload",
    response_model=IngestionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a PDF, HTML, Markdown or text document as a source",
)
async def upload_source(
    product_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="PDF, HTML, .md or .txt")],
    source_type: Annotated[SourceType, Form()] = SourceType.USER_DOCUMENT,
    title: Annotated[str | None, Form(max_length=300)] = None,
    workspace_id: Annotated[uuid.UUID | None, Form()] = None,
) -> IngestionResponse:
    # Read at most limit+1 bytes: never buffer an arbitrarily large upload.
    content = await file.read(settings.ingestion_max_bytes + 1)
    if len(content) > settings.ingestion_max_bytes:
        raise FileTooLargeError
    result = await SourceService(session, settings).upload(
        user,
        product_id=product_id,
        workspace_id=workspace_id,
        source_type=source_type,
        title=title,
        filename=file.filename,
        declared_content_type=file.content_type,
        content=content,
    )
    return _ingestion(result)


@router.post(
    "/sources/{source_id}/ingest",
    response_model=IngestionResponse,
    summary="Fetch the source URL safely, snapshot it and extract text",
    responses={
        422: {"description": "URL blocked (SSRF protection) or content unparseable"},
        502: {"description": "Upstream fetch failed"},
    },
)
async def ingest_source(
    source_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    fetcher: FetcherDep,
) -> IngestionResponse:
    result = await SourceService(session, settings).ingest_url(source_id, user, fetcher)
    return _ingestion(result)


@router.get("/sources/{source_id}", response_model=SourceDetail)
async def get_source(
    source_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> SourceDetail:
    service = SourceService(session, settings)
    source = await service.get_source(source_id, user)
    document = await service.latest_document(source_id, user)
    return SourceDetail(
        **SourceResponse.model_validate(source).model_dump(),
        latest_document=DocumentMeta.model_validate(document) if document else None,
    )


@router.get("/sources/{source_id}/document", response_model=DocumentText)
async def get_document(
    source_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> DocumentText:
    document = await SourceService(session, settings).latest_document(
        source_id, user, with_text=True
    )
    if document is None:
        raise SourceNotFoundError("This source has not been ingested yet.")
    return DocumentText.model_validate(document)
