"""Workspace request/response contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.roles import WorkspaceRole


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class WorkspaceUpdate(BaseModel):
    """Partial update: only fields present in the request body are changed."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _name_cannot_be_null(self) -> WorkspaceUpdate:
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        return self


class WorkspaceResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: str | None
    created_by_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    my_role: WorkspaceRole


class WorkspaceMemberResponse(BaseModel):
    user_id: uuid.UUID
    display_name: str
    email: str
    role: WorkspaceRole
    joined_at: datetime


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class WorkspaceListResponse(BaseModel):
    items: list[WorkspaceResponse]
    page: PageMeta
