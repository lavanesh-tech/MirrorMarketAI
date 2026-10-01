"""Request/response models for comments, votes and presence."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.collaboration import COMMENT_MAX_CHARS
from app.schemas.workspaces import PageMeta


class CommentBody(BaseModel):
    body: str = Field(min_length=1, max_length=COMMENT_MAX_CHARS)

    @field_validator("body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("comment must not be blank")
        return value


class CommentCreate(CommentBody):
    product_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None


class CommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    product_id: uuid.UUID | None
    parent_id: uuid.UUID | None
    author_id: uuid.UUID
    author_name: str
    body: str | None  # None once deleted
    deleted: bool
    edited_at: datetime | None
    created_at: datetime


class CommentListResponse(BaseModel):
    items: list[CommentResponse]
    page: PageMeta


class VoteIn(BaseModel):
    value: Literal[-1, 0, 1] = Field(description="1 = up, -1 = down, 0 = remove my vote")


class VoteTally(BaseModel):
    product_id: uuid.UUID
    up: int
    down: int
    score: int
    my_vote: int  # -1, 0 or 1


class VoteTallyList(BaseModel):
    items: list[VoteTally]


class PresenceResponse(BaseModel):
    workspace_id: uuid.UUID
    user_ids: list[uuid.UUID]
