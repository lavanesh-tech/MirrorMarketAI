"""Purchase-requirement contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.requirements import Criterion, FieldChange, RequirementSpec
from app.schemas.workspaces import PageMeta

MAX_TEXT_CHARS = 20_000


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class ExtractResponse(BaseModel):
    spec: RequirementSpec
    unparsed: list[str]
    extractor: str
    degraded: bool = Field(description="True when the configured extractor failed and rules ran")


class SaveRequirementsRequest(BaseModel):
    """Send `text` to extract, `spec` to save an edited spec, or both (spec wins, text kept)."""

    text: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT_CHARS)
    spec: RequirementSpec | None = None
    expected_version: int = Field(ge=0, description="The current_version you last read (0 = none)")
    change_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _something_to_save(self) -> SaveRequirementsRequest:
        if self.text is None and self.spec is None:
            raise ValueError("send text, spec, or both")
        return self


class RequirementVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    raw_text: str | None
    spec: RequirementSpec
    unparsed: list[str]
    extractor: str
    change_note: str | None
    created_by_id: uuid.UUID
    created_at: datetime


class RequirementsResponse(BaseModel):
    workspace_id: uuid.UUID
    current_version: int
    current: RequirementVersionResponse
    degraded: bool = False


class RequirementVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    extractor: str
    change_note: str | None
    created_by_id: uuid.UUID
    created_at: datetime


class RequirementVersionListResponse(BaseModel):
    items: list[RequirementVersionSummary]
    page: PageMeta


class RequirementDiffResponse(BaseModel):
    from_version: int
    to_version: int
    changes: list[FieldChange]
    criteria_added: list[Criterion]
    criteria_removed: list[Criterion]
    criteria_changed: list[FieldChange]
