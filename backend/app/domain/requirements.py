"""Purchase requirements: the structured form of "what the buyer needs".

A `RequirementSpec` is what the comparison engine (Phase 16) scores products
against. Criteria use the same snake_case keys as product specifications
(e.g. `ram_gb`, `weight_kg`), so a criterion can be checked against a spec row
without translation.

This module is pure (no I/O) and fully unit-tested.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.products import is_valid_spec_key
from app.models.catalog import PRODUCT_CATEGORIES

MAX_CRITERIA = 50
MAX_LIST_ITEMS = 20


class Priority(StrEnum):
    MUST = "MUST"  # hard constraint: a product failing it is excluded
    SHOULD = "SHOULD"  # soft preference: affects the score, never excludes


class Operator(StrEnum):
    LTE = "<="
    GTE = ">="
    EQ = "="
    NEQ = "!="


class Criterion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, max_length=64, examples=["ram_gb"])
    operator: Operator
    value_number: Decimal | None = Field(default=None, max_digits=18, decimal_places=6)
    value_text: str | None = Field(default=None, min_length=1, max_length=200)
    unit: str | None = Field(default=None, max_length=20)
    priority: Priority = Priority.SHOULD
    weight: int = Field(default=3, ge=1, le=5, description="Importance 1-5 (SHOULD only)")

    @field_validator("key")
    @classmethod
    def _snake_case(cls, value: str) -> str:
        if not is_valid_spec_key(value):
            raise ValueError("key must be snake_case, e.g. ram_gb")
        return value

    @model_validator(mode="after")
    def _one_value(self) -> Criterion:
        if (self.value_number is None) == (self.value_text is None):
            raise ValueError("exactly one of value_number or value_text is required")
        if self.value_text is not None and self.operator in (Operator.LTE, Operator.GTE):
            raise ValueError("<= and >= need a numeric value")
        return self


class Budget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    min_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    max_amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def _range(self) -> Budget:
        if self.min_amount is None and self.max_amount is None:
            raise ValueError("a budget needs min_amount or max_amount")
        if (
            self.min_amount is not None
            and self.max_amount is not None
            and self.min_amount > self.max_amount
        ):
            raise ValueError("min_amount must not exceed max_amount")
        return self


def _clean_list(values: list[str]) -> list[str]:
    """Trim, drop blanks and case-insensitive duplicates, keep first spelling and order."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = " ".join(raw.split())
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            result.append(value)
    return result


class RequirementSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    category: str | None = None
    budget: Budget | None = None
    criteria: list[Criterion] = Field(default_factory=list, max_length=MAX_CRITERIA)
    excluded_brands: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    use_cases: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str | None) -> str | None:
        if value is not None and value not in PRODUCT_CATEGORIES:
            raise ValueError(f"category must be one of {', '.join(PRODUCT_CATEGORIES)}")
        return value

    @field_validator("excluded_brands", "use_cases")
    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        return _clean_list(values)

    @field_validator("criteria")
    @classmethod
    def _unique_criteria(cls, criteria: list[Criterion]) -> list[Criterion]:
        seen: set[tuple[str, Operator]] = set()
        for criterion in criteria:
            identity = (criterion.key, criterion.operator)
            if identity in seen:
                raise ValueError(f"duplicate criterion {criterion.key} {criterion.operator}")
            seen.add(identity)
        return criteria

    @property
    def hard_constraints(self) -> list[Criterion]:
        return [c for c in self.criteria if c.priority is Priority.MUST]

    def to_json(self) -> dict[str, Any]:
        """JSON-safe dict for JSONB storage (Decimals become strings, no precision loss)."""
        return self.model_dump(mode="json")


# --------------------------------------------------------------------------- diff
class FieldChange(BaseModel):
    field: str
    before: Any
    after: Any


class RequirementDiff(BaseModel):
    changes: list[FieldChange]
    criteria_added: list[Criterion]
    criteria_removed: list[Criterion]
    criteria_changed: list[FieldChange]

    @property
    def is_empty(self) -> bool:
        return not (
            self.changes or self.criteria_added or self.criteria_removed or self.criteria_changed
        )


def diff_specs(before: RequirementSpec, after: RequirementSpec) -> RequirementDiff:
    """Field-level diff; criteria are matched by (key, operator)."""
    changes = [
        FieldChange(field=name, before=old, after=new)
        for name in ("category", "budget", "excluded_brands", "use_cases", "notes")
        if (old := _jsonable(getattr(before, name))) != (new := _jsonable(getattr(after, name)))
    ]
    old = {(c.key, c.operator): c for c in before.criteria}
    new = {(c.key, c.operator): c for c in after.criteria}
    return RequirementDiff(
        changes=changes,
        criteria_added=[c for k, c in new.items() if k not in old],
        criteria_removed=[c for k, c in old.items() if k not in new],
        criteria_changed=[
            FieldChange(
                field=f"{key} {operator}",
                before=old[(key, operator)].model_dump(mode="json"),
                after=criterion.model_dump(mode="json"),
            )
            for (key, operator), criterion in new.items()
            if (key, operator) in old and old[(key, operator)] != criterion
        ],
    )


def _jsonable(value: Any) -> Any:
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value
