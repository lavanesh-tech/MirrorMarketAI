"""Purchase requirements with immutable versions.

    ComparisonWorkspace 1──1 PurchaseRequirement 1──* RequirementVersion

Every edit inserts a new RequirementVersion (never UPDATE), so recommendations
can always cite exactly which requirements they were computed against, and the
history can be diffed. `current_version` on the parent is the optimistic-lock
counter: a writer must send the version it read, or gets 409.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PurchaseRequirement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "purchase_requirements"
    __table_args__ = (
        UniqueConstraint("workspace_id"),
        CheckConstraint("current_version >= 0", name="current_version_valid"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comparison_workspaces.id", ondelete="CASCADE")
    )
    current_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )


class RequirementVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "requirement_versions"
    __table_args__ = (
        UniqueConstraint("requirement_id", "version"),
        CheckConstraint("version >= 1", name="version_valid"),
    )

    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_requirements.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    raw_text: Mapped[str | None] = mapped_column(Text)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    unparsed: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    extractor: Mapped[str] = mapped_column(String(100))
    change_note: Mapped[str | None] = mapped_column(String(500))
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
