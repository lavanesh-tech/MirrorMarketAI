"""Evidence packs: frozen, citable snapshots of retrieved chunks.

    ComparisonWorkspace 1──* EvidencePack 1──* EvidenceItem (position 1..n = marker E1..En)

Items copy the chunk text and source metadata, so a citation still resolves to
the exact words that were shown even after a source is re-ingested or deleted.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EvidencePack(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evidence_packs"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comparison_workspaces.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    query: Mapped[str] = mapped_column(String(500))
    mode: Mapped[str] = mapped_column(String(16))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    requirement_version: Mapped[int | None] = mapped_column(Integer)
    degraded: Mapped[bool] = mapped_column(default=False, server_default="false")

    items: Mapped[list[EvidenceItem]] = relationship(
        order_by="EvidenceItem.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class EvidenceItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evidence_items"
    __table_args__ = (
        UniqueConstraint("pack_id", "position"),
        CheckConstraint("position >= 1", name="position_valid"),
    )

    pack_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_packs.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="SET NULL"), index=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_sources.id", ondelete="SET NULL"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    source_title: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(String(2048))
    authority: Mapped[str] = mapped_column(String(16))
    source_type: Mapped[str] = mapped_column(String(32))
    score: Mapped[float] = mapped_column(Float)
