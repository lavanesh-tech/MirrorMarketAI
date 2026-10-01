"""Shared agent contracts. Agents read evidence packs and must cite them ([E1] markers)."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.citations import CitationReport
from app.domain.requirements import Criterion


class FactStatus(StrEnum):
    MET = "MET"
    UNMET = "UNMET"
    UNKNOWN = "UNKNOWN"  # no value found in evidence or catalog
    NOT_COMPARABLE = "NOT_COMPARABLE"  # a value exists but cannot be compared (e.g. text vs number)
    NO_REQUIREMENT = "NO_REQUIREMENT"  # researched by default, nothing to compare against


class Fact(BaseModel):
    key: str
    label: str
    value_number: Decimal | None = None
    value_text: str | None = None
    unit: str | None = None
    source: Literal["evidence", "catalog"] | None = None
    citations: list[str] = Field(default_factory=list)
    requirement: Criterion | None = None
    status: FactStatus


class ResearchOutput(BaseModel):
    product_id: str
    summary: str
    facts: list[Fact]
    found: int
    total: int


class Polarity(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"


class ReviewExample(BaseModel):
    marker: str
    sentence: str
    polarity: Polarity


class AspectSummary(BaseModel):
    aspect: str
    label: str
    positive: int
    negative: int
    sentiment: Polarity
    citations: list[str]
    examples: list[ReviewExample]


class ReviewOutput(BaseModel):
    product_id: str
    summary: str
    aspects: list[AspectSummary]
    praises: list[str]
    complaints: list[str]
    review_chunks: int
    opinion_sentences: int
    overall: float = Field(description="(positive - negative) / mentions, from -1 to 1")


class AgentResult(BaseModel):
    output: BaseModel
    validation: CitationReport
    engine: str
    degraded: bool = False
    tokens_used: int = 0
