"""Evidence sources: where product information comes from.

    ProductSource 1──* SourceSnapshot 1──1 SourceDocument

- ProductSource: a URL or uploaded file about a product. `workspace_id` NULL
  means it's a shared catalog source; otherwise it's private to that workspace.
- SourceSnapshot: the exact bytes retrieved at a point in time (with sha256).
  Re-ingesting unchanged content reuses the existing snapshot.
- SourceDocument: normalized plain text parsed from one snapshot. Chunking and
  embeddings (Phase 6) are built from documents.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SourceType(StrEnum):
    MANUFACTURER_PAGE = "MANUFACTURER_PAGE"
    SPECIFICATION_SHEET = "SPECIFICATION_SHEET"
    MANUAL = "MANUAL"
    WARRANTY = "WARRANTY"
    RETURN_POLICY = "RETURN_POLICY"
    REVIEW = "REVIEW"
    USER_DOCUMENT = "USER_DOCUMENT"
    OTHER = "OTHER"


class SourceAuthority(StrEnum):
    OFFICIAL = "OFFICIAL"  # manufacturer / official documentation
    THIRD_PARTY = "THIRD_PARTY"  # reviewers, retailers
    USER = "USER"  # uploaded or entered by a user


class SourceStatus(StrEnum):
    PENDING = "PENDING"
    INGESTED = "INGESTED"
    FAILED = "FAILED"


def _in(column: str, enum_cls: type[StrEnum]) -> CheckConstraint:
    allowed = ", ".join(f"'{m.value}'" for m in enum_cls)
    return CheckConstraint(f"{column} IN ({allowed})", name=f"{column}_valid")


class ProductSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_sources"
    __table_args__ = (
        _in("source_type", SourceType),
        _in("authority", SourceAuthority),
        _in("status", SourceStatus),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("comparison_workspaces.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(32))
    authority: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(16), default=SourceStatus.PENDING.value)
    last_error: Mapped[str | None] = mapped_column(String(500))
    last_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    snapshots: Mapped[list[SourceSnapshot]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SourceSnapshot.last_seen_at.desc()",
    )


class SourceSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (
        UniqueConstraint("source_id", "sha256"),
        CheckConstraint("byte_size >= 0", name="byte_size_non_negative"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_sources.id", ondelete="CASCADE"), index=True
    )
    # First time these exact bytes were retrieved.
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Most recent time a fetch/upload returned these bytes. "Latest document"
    # is chosen by this, so content that reverts (A -> B -> A) resolves to A.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    content_type: Mapped[str] = mapped_column(String(100))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    # Raw bytes kept for reproducibility/re-parsing. Moves to S3 in Phase 31.
    raw_content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    final_url: Mapped[str | None] = mapped_column(String(2048))
    http_status: Mapped[int | None] = mapped_column(Integer)
    original_filename: Mapped[str | None] = mapped_column(String(255))

    source: Mapped[ProductSource] = relationship(back_populates="snapshots")
    document: Mapped[SourceDocument | None] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )


class SourceDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "source_documents"
    __table_args__ = (UniqueConstraint("snapshot_id"),)

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="CASCADE")
    )
    # Denormalized for fast, filterable retrieval (Phase 7 metadata filters).
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_sources.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("comparison_workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text, deferred=True)
    char_count: Mapped[int] = mapped_column(Integer)
    parser: Mapped[str] = mapped_column(String(16))

    snapshot: Mapped[SourceSnapshot] = relationship(back_populates="document")
