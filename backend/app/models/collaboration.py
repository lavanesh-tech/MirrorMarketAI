"""Collaboration inside a workspace: comments and product votes.

Both are tenant data (every row carries workspace_id). A comment may target one
product in the workspace or the workspace itself (product_id NULL). Deleting a
comment is a soft delete, so replies keep their parent and the audit trail stays.
A vote is one row per (workspace, product, user); changing your mind updates it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import User

COMMENT_MAX_CHARS = 4000


class WorkspaceComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_comments"
    __table_args__ = (
        CheckConstraint(f"char_length(body) BETWEEN 1 AND {COMMENT_MAX_CHARS}", name="body_length"),
        Index("ix_workspace_comments_thread", "workspace_id", "product_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comparison_workspaces.id", ondelete="CASCADE")
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace_comments.id", ondelete="CASCADE"), index=True
    )
    body: Mapped[str] = mapped_column(Text)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    author: Mapped[User] = relationship(lazy="joined")


class ProductVote(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_votes"
    __table_args__ = (
        UniqueConstraint("workspace_id", "product_id", "user_id"),
        CheckConstraint("value IN (-1, 1)", name="value_valid"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comparison_workspaces.id", ondelete="CASCADE")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    value: Mapped[int] = mapped_column(SmallInteger)
