"""Identity and tenancy tables: users, organizations, workspaces, memberships.

Tenancy model:
    Organization (tenant)  1──*  ComparisonWorkspace  1──*  WorkspaceMember  *──1  User
    Organization           1──*  OrganizationMember   *──1  User

Every user gets a personal organization at registration. Workspace access is
decided by WorkspaceMember rows only; there is no implicit access.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.roles import OrganizationRole, WorkspaceRole
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


def _role_enum(enum_cls: type[StrEnum]) -> Enum:
    # VARCHAR + explicit CHECK (see _role_check) instead of a native PG enum:
    # adding a role later is a simple migration instead of ALTER TYPE gymnastics.
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=16,
        values_callable=lambda e: [member.value for member in e],
        validate_strings=True,
    )


def _role_check(enum_cls: type[StrEnum]) -> CheckConstraint:
    allowed = ", ".join(f"'{member.value}'" for member in enum_cls)
    return CheckConstraint(f"role IN ({allowed})", name="role_valid")


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email"),
        # The service lower-cases emails; the database guarantees it, so the
        # plain unique constraint also blocks case-variant duplicates.
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Embedded in access tokens as `ver`; bumping it rejects every token issued so far
    # ("log out everywhere", password change) without keeping a token denylist.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(100))
    is_personal: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )


class OrganizationMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id"),
        _role_check(OrganizationRole),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[OrganizationRole] = mapped_column(_role_enum(OrganizationRole))


class ComparisonWorkspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "comparison_workspaces"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)

    members: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan", passive_deletes=True
    )


class WorkspaceMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id"),
        _role_check(WorkspaceRole),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comparison_workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[WorkspaceRole] = mapped_column(_role_enum(WorkspaceRole))

    workspace: Mapped[ComparisonWorkspace] = relationship(back_populates="members")
    user: Mapped[User] = relationship(lazy="joined")
