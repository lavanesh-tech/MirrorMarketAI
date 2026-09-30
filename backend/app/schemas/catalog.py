"""Product catalog request/response contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.products import IdentifierScheme, is_valid_spec_key
from app.models.catalog import PRODUCT_CATEGORIES
from app.schemas.workspaces import PageMeta

Category = Literal[
    "laptop",
    "desktop",
    "monitor",
    "phone",
    "tablet",
    "headphones",
    "camera",
    "appliance",
    "other",
]
assert set(Category.__args__) == set(PRODUCT_CATEGORIES)  # type: ignore[attr-defined]  # noqa: S101

_STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProductCreate(BaseModel):
    model_config = _STRICT

    brand: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    category: Category
    description: str | None = Field(default=None, max_length=5000)


class VariantCreate(BaseModel):
    model_config = _STRICT

    name: str = Field(min_length=1, max_length=120)
    # Small, flat attribute map, e.g. {"ram_gb": 16, "color": "Midnight"}.
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=30)

    @field_validator("attributes")
    @classmethod
    def _keys_are_spec_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        bad = [k for k in value if not is_valid_spec_key(k)]
        if bad:
            raise ValueError(f"attribute keys must be snake_case: {bad}")
        return value


class IdentifierCreate(BaseModel):
    model_config = _STRICT

    scheme: IdentifierScheme
    value: str = Field(min_length=1, max_length=64)
    variant_id: uuid.UUID | None = None


class SpecificationInput(BaseModel):
    model_config = _STRICT

    key: str = Field(min_length=1, max_length=64)
    value_number: Decimal | None = Field(default=None, max_digits=18, decimal_places=6)
    value_text: str | None = Field(default=None, min_length=1, max_length=500)
    unit: str | None = Field(default=None, min_length=1, max_length=16)
    variant_id: uuid.UUID | None = None

    @field_validator("key")
    @classmethod
    def _snake_case(cls, value: str) -> str:
        if not is_valid_spec_key(value):
            raise ValueError("key must be snake_case, e.g. ram_gb")
        return value

    @model_validator(mode="after")
    def _exactly_one_value(self) -> SpecificationInput:
        if (self.value_number is None) == (self.value_text is None):
            raise ValueError("provide exactly one of value_number or value_text")
        if self.value_number is not None and not self.value_number.is_finite():
            raise ValueError("value_number must be finite")
        return self


class SpecificationsUpsert(BaseModel):
    model_config = _STRICT

    specifications: list[SpecificationInput] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _no_duplicate_keys(self) -> SpecificationsUpsert:
        seen = {(s.variant_id, s.key) for s in self.specifications}
        if len(seen) != len(self.specifications):
            raise ValueError("duplicate (variant_id, key) in request")
        return self


class VariantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    attributes: dict[str, Any]


class IdentifierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scheme: IdentifierScheme
    value: str
    variant_id: uuid.UUID | None


class SpecificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value_number: Decimal | None
    value_text: str | None
    unit: str | None
    variant_id: uuid.UUID | None


class ProductSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand: str
    name: str
    category: str
    created_at: datetime


class ProductDetail(ProductSummary):
    description: str | None
    created_by_id: uuid.UUID
    variants: list[VariantResponse]
    identifiers: list[IdentifierResponse]
    specifications: list[SpecificationResponse]


class ProductListResponse(BaseModel):
    items: list[ProductSummary]
    page: PageMeta


class WorkspaceProductAdd(BaseModel):
    model_config = _STRICT

    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)


class WorkspaceProductResponse(BaseModel):
    product: ProductSummary
    variant_id: uuid.UUID | None
    notes: str | None
    added_by_id: uuid.UUID
    added_at: datetime
