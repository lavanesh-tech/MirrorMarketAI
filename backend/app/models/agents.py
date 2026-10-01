"""Agent runs: one row per agent execution, with its cited output and validation report."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

AGENT_RUN_STATUSES = ("SUCCEEDED", "FAILED")


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in AGENT_RUN_STATUSES) + ")",
            name="status_valid",
        ),
        Index("ix_agent_runs_workspace_agent_created", "workspace_id", "agent", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comparison_workspaces.id", ondelete="CASCADE")
    )
    agent: Mapped[str] = mapped_column(String(40))
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    evidence_pack_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_packs.id", ondelete="SET NULL"), index=True
    )
    requirement_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    engine: Mapped[str] = mapped_column(String(100))
    degraded: Mapped[bool] = mapped_column(default=False, server_default="false")
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(String(500))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
