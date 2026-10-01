"""Source registration, safe URL ingestion, uploads and document access.

Access rules
- Shared source (workspace_id NULL): any signed-in user can read it; only the
  product's creator can create or re-ingest it.
- Workspace source: only workspace members can read it (non-members get 404);
  OWNER/EDITOR can create, upload and ingest.

Ingestion runs inside the request for now (bounded by size and timeout
limits). It moves to background workers with Kafka in Phase 21.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, undefer

from app.core.config import Settings
from app.core.errors import (
    PermissionDeniedError,
    ProductNotFoundError,
    SourceFetchFailedError,
    SourceHasNoUrlError,
    SourceNotFoundError,
    SourceUnparseableError,
    UnsafeUrlError,
)
from app.domain.roles import WorkspaceRole
from app.ingestion.parsers import ParseError, parse_document, sniff_content_type
from app.ingestion.safe_fetch import FetchError, SafeFetcher, UnsafeURLError, validate_url_syntax
from app.models.catalog import Product
from app.models.identity import User, WorkspaceMember
from app.models.sources import (
    ProductSource,
    SourceAuthority,
    SourceDocument,
    SourceSnapshot,
    SourceStatus,
    SourceType,
)
from app.security import audit_trail as audit
from app.security.files import UnsafeFileError, check_upload, sanitize_filename
from app.services.embeddings import embedding_model_name, enqueue_embedding_job
from app.services.workspaces import WorkspaceService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestionResult:
    source: ProductSource
    snapshot: SourceSnapshot
    document: SourceDocument
    unchanged: bool


class SourceService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.workspaces = WorkspaceService(session)

    # ------------------------------------------------------------------ access
    async def _product(self, product_id: uuid.UUID) -> Product:
        product = await self.session.get(Product, product_id)
        if product is None:
            raise ProductNotFoundError
        return product

    async def require_write(
        self, product: Product, workspace_id: uuid.UUID | None, user: User
    ) -> None:
        if workspace_id is not None:
            await self.workspaces.authorize(workspace_id, user, WorkspaceRole.EDITOR)
        elif product.created_by_id != user.id:
            raise PermissionDeniedError(
                "Only the product's creator can add shared sources; use a workspace source."
            )

    async def _is_member(self, workspace_id: uuid.UUID, user: User) -> bool:
        member = await self.session.scalar(
            select(WorkspaceMember.id).where(
                WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user.id
            )
        )
        return member is not None

    async def get_source(self, source_id: uuid.UUID, user: User) -> ProductSource:
        source = await self.session.get(ProductSource, source_id)
        # Hide workspace sources from non-members exactly like missing ones.
        if source is None or (
            source.workspace_id is not None and not await self._is_member(source.workspace_id, user)
        ):
            raise SourceNotFoundError
        return source

    async def list_for_product(self, product_id: uuid.UUID, user: User) -> list[ProductSource]:
        await self._product(product_id)
        my_workspaces = select(WorkspaceMember.workspace_id).where(
            WorkspaceMember.user_id == user.id
        )
        rows = await self.session.scalars(
            select(ProductSource)
            .where(
                ProductSource.product_id == product_id,
                or_(
                    ProductSource.workspace_id.is_(None),
                    ProductSource.workspace_id.in_(my_workspaces),
                ),
            )
            .order_by(ProductSource.created_at, ProductSource.id)
        )
        return list(rows)

    async def latest_document(
        self, source_id: uuid.UUID, user: User, *, with_text: bool = False
    ) -> SourceDocument | None:
        await self.get_source(source_id, user)
        stmt = (
            select(SourceDocument)
            .join(SourceSnapshot, SourceSnapshot.id == SourceDocument.snapshot_id)
            .where(SourceDocument.source_id == source_id)
            .order_by(SourceSnapshot.last_seen_at.desc(), SourceSnapshot.id.desc())
            .limit(1)
        )
        if with_text:
            stmt = stmt.options(undefer(SourceDocument.text))
        document: SourceDocument | None = await self.session.scalar(stmt)
        return document

    # ---------------------------------------------------------------- creation
    async def create_source(
        self,
        user: User,
        *,
        product_id: uuid.UUID,
        workspace_id: uuid.UUID | None,
        source_type: SourceType,
        authority: SourceAuthority,
        title: str,
        url: str | None,
    ) -> ProductSource:
        product = await self._product(product_id)
        await self.require_write(product, workspace_id, user)
        if url is not None:
            try:
                validate_url_syntax(url, self.settings.ingestion_allowed_ports)
            except UnsafeURLError as exc:
                raise UnsafeUrlError(str(exc)) from exc
        source = ProductSource(
            product_id=product.id,
            workspace_id=workspace_id,
            created_by_id=user.id,
            source_type=source_type.value,
            authority=authority.value,
            title=title,
            url=url,
            status=SourceStatus.PENDING.value,
        )
        self.session.add(source)
        await self.session.commit()
        return source

    # --------------------------------------------------------------- ingestion
    async def ingest_url(
        self, source_id: uuid.UUID, user: User, fetcher: SafeFetcher
    ) -> IngestionResult:
        source = await self.get_source(source_id, user)
        product = await self._product(source.product_id)
        await self.require_write(product, source.workspace_id, user)
        if not source.url:
            raise SourceHasNoUrlError
        try:
            fetched = await fetcher.fetch(source.url)
        except UnsafeURLError as exc:
            await self._mark_failed(source, f"blocked: {exc}")
            raise UnsafeUrlError(str(exc)) from exc
        except FetchError as exc:
            await self._mark_failed(source, str(exc))
            raise SourceFetchFailedError(str(exc)) from exc
        return await self._store(
            source,
            content=fetched.content,
            content_type=fetched.content_type,
            final_url=fetched.final_url,
            http_status=fetched.status_code,
            filename=None,
        )

    async def upload(
        self,
        user: User,
        *,
        product_id: uuid.UUID,
        workspace_id: uuid.UUID | None,
        source_type: SourceType,
        title: str | None,
        filename: str | None,
        declared_content_type: str | None,
        content: bytes,
    ) -> IngestionResult:
        safe_name = sanitize_filename(filename)
        try:
            check_upload(content)
            content_type = sniff_content_type(content, declared_content_type)
        except (UnsafeFileError, ParseError) as exc:
            audit.record(
                self.session,
                audit.SOURCE_REJECTED,
                outcome=audit.FAILURE,
                actor_id=user.id,
                workspace_id=workspace_id,
                target_type="product",
                target_id=product_id,
                details={"reason": str(exc)[:200], "filename": safe_name, "bytes": len(content)},
            )
            await self.session.commit()
            raise SourceUnparseableError(str(exc)) from exc
        source = await self.create_source(
            user,
            product_id=product_id,
            workspace_id=workspace_id,
            source_type=source_type,
            authority=SourceAuthority.USER,  # uploads are never "official"
            title=title or safe_name,
            url=None,
        )
        audit.record(
            self.session,
            audit.SOURCE_UPLOADED,
            actor_id=user.id,
            workspace_id=workspace_id,
            target_type="source",
            target_id=source.id,
            details={"filename": safe_name, "bytes": len(content), "content_type": content_type},
        )
        return await self._store(
            source,
            content=content,
            content_type=content_type,
            final_url=None,
            http_status=None,
            filename=safe_name,
        )

    async def _mark_failed(self, source: ProductSource, error: str) -> None:
        source.status = SourceStatus.FAILED.value
        source.last_error = error[:500]
        await self.session.commit()
        logger.info("source ingestion failed", extra={"source_id": str(source.id)})

    async def _store(
        self,
        source: ProductSource,
        *,
        content: bytes,
        content_type: str,
        final_url: str | None,
        http_status: int | None,
        filename: str | None,
    ) -> IngestionResult:
        digest = hashlib.sha256(content).hexdigest()
        existing = await self.session.scalar(
            select(SourceSnapshot)
            .options(selectinload(SourceSnapshot.document))
            .where(SourceSnapshot.source_id == source.id, SourceSnapshot.sha256 == digest)
        )
        # Wall-clock time (not the DB's transaction-start now()), so two ingests
        # in one transaction still order correctly.
        now = datetime.now(UTC)
        if existing is not None and existing.document is not None:
            existing.last_seen_at = now
            source.status = SourceStatus.INGESTED.value
            source.last_error = None
            source.last_ingested_at = now
            await self.session.commit()
            return IngestionResult(source, existing, existing.document, unchanged=True)

        try:
            parsed = parse_document(
                content, content_type, max_pdf_pages=self.settings.ingestion_max_pdf_pages
            )
        except ParseError as exc:
            await self._mark_failed(source, f"parse error: {exc}")
            raise SourceUnparseableError(str(exc)) from exc
        if len(parsed.text) > self.settings.ingestion_max_text_chars:
            # A small file can expand into a huge amount of text (decompression bomb).
            await self._mark_failed(source, "extracted text too large")
            raise SourceUnparseableError("The document contains too much text.")

        snapshot = SourceSnapshot(
            source_id=source.id,
            content_type=content_type,
            byte_size=len(content),
            sha256=digest,
            fetched_at=now,
            last_seen_at=now,
            raw_content=content,
            final_url=final_url,
            http_status=http_status,
            original_filename=filename,
        )
        self.session.add(snapshot)
        await self.session.flush()
        document = SourceDocument(
            snapshot_id=snapshot.id,
            source_id=source.id,
            product_id=source.product_id,
            workspace_id=source.workspace_id,
            title=(parsed.title or source.title)[:300],
            text=parsed.text,
            char_count=len(parsed.text),
            parser=parsed.parser,
        )
        self.session.add(document)
        await self.session.flush()
        # Queue chunking + embedding in the same transaction as the document:
        # either both exist or neither does (no lost work, no orphan jobs).
        await enqueue_embedding_job(self.session, document.id, embedding_model_name(self.settings))
        source.status = SourceStatus.INGESTED.value
        source.last_error = None
        source.last_ingested_at = now
        await self.session.commit()
        logger.info(
            "source ingested",
            extra={"source_id": str(source.id), "bytes": len(content), "chars": len(parsed.text)},
        )
        return IngestionResult(source, snapshot, document, unchanged=False)
