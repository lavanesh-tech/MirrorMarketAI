"""Risk Agent: purchase risks from evidence plus other agents' verdicts.

Evidence risks are detected sentence by sentence with deterministic patterns:
warranty length, return policy, safety and recalls, reliability reports,
repairability and software support. Cross-agent risks come from the latest
Compatibility (INCOMPATIBLE checks), Value (over budget) and Product Research
(MUST criteria that are UNMET or unverified) runs. Severity is always assigned in
code, never by a model.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, field_serializer

from app.domain.citations import split_sentences
from app.domain.requirements import Priority, RequirementSpec
from app.models.catalog import Product


class Severity(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class Category(StrEnum):
    WARRANTY = "warranty"
    RETURNS = "returns"
    SAFETY = "safety"
    RELIABILITY = "reliability"
    REPAIRABILITY = "repairability"
    SUPPORT = "support"
    COMPATIBILITY = "compatibility"
    BUDGET = "budget"
    REQUIREMENTS = "requirements"


# One search per evidence category (product-filtered). `or` makes full-text match any term.
QUERIES: dict[Category, str] = {
    Category.WARRANTY: "warranty or guarantee",
    Category.RETURNS: "return or returns or refund or restocking",
    Category.SAFETY: "recall or fire or overheating or swelling or burn",
    Category.RELIABILITY: "broke or died or defect or failure or cracked or dead",
    Category.REPAIRABILITY: "soldered or replaceable or repair or glued or upgradeable",
    Category.SUPPORT: "updates or support or discontinued or end-of-life",
}

_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ninety": 90}
_DURATION = re.compile(
    r"\b(\d{1,3}|one|two|three|four|five|six|ninety)[- ]?(day|month|year)s?\b", re.I
)
_NO_WARRANTY = re.compile(
    r"\b(no|without|void(?:s|ed)?)\s+(?:\w+\s+)?warranty\b|sold as[- ]is", re.I
)
_NO_RETURNS = re.compile(
    r"\b(no returns|non-?returnable|all sales (?:are )?final|cannot be returned)\b", re.I
)
_RESTOCKING = re.compile(r"\brestocking fee\b", re.I)
_SAFETY = re.compile(
    r"\b(recall(?:ed|s)?|fire|caught fire|burn(?:ed|s|t)?|swell(?:ing|ed|s)?|melt(?:ed|ing)?|"
    r"shock|explod\w*|overheat\w*|smok(?:e|ing))\b",
    re.I,
)
_RELIABILITY = re.compile(
    r"\b(broke|broken|died|dead|defect\w*|stopped working|failed|failure|rma|"
    r"replacement unit|bricked|dead pixels?|hinge cracked)\b",
    re.I,
)
_REPAIR = re.compile(
    r"\b(soldered|glued|non-?replaceable|not (?:user[- ])?replaceable|not upgradeable|"
    r"proprietary screws|cannot be repaired)\b",
    re.I,
)
_SUPPORT = re.compile(
    r"\b(end[- ]of[- ]life|discontinued|no (?:more |further )?(?:software |security )?updates|"
    r"support ends|unsupported)\b",
    re.I,
)
_NEGATED = re.compile(r"\b(no|never|not|without|zero)\b(?:\s+\w+){0,2}\s*$", re.I)

MIN_WARRANTY_MONTHS = 12
MIN_RETURN_DAYS = 14
RELIABILITY_HIGH_ITEMS = 2


class Risk(BaseModel):
    category: Category
    severity: Severity
    title: str
    citations: list[str] = []
    quote: str | None = None
    source: str  # "evidence" | agent name

    @field_serializer("severity")
    def _severity_name(self, severity: Severity) -> str:
        return severity.name


class RiskOutput(BaseModel):
    product_id: str
    level: str  # NONE | LOW | MEDIUM | HIGH
    score: int  # sum of severities
    summary: str
    risks: list[Risk]
    checked_categories: list[str]


@dataclass(frozen=True, slots=True)
class Hit:
    category: Category
    severity: Severity
    title: str
    position: int
    quote: str


def _months(number: str, unit: str) -> float:
    value = _WORD_NUMBERS.get(number.lower()) or int(number)
    return {"day": value / 30, "month": value, "year": value * 12}[unit.lower()]


def classify_sentence(sentence: str) -> list[tuple[Category, Severity, str]]:
    """(category, severity, title) risks stated in one sentence."""
    found: list[tuple[Category, Severity, str]] = []
    lowered = sentence.lower()
    if "warranty" in lowered:
        if _NO_WARRANTY.search(sentence):
            found.append((Category.WARRANTY, Severity.HIGH, "No warranty coverage"))
        elif (m := _DURATION.search(sentence)) and _months(*m.groups()) < MIN_WARRANTY_MONTHS:
            found.append((Category.WARRANTY, Severity.MEDIUM, "Short warranty coverage"))
    if "return" in lowered or "refund" in lowered or "sales" in lowered:
        if _NO_RETURNS.search(sentence):
            found.append((Category.RETURNS, Severity.MEDIUM, "No returns accepted"))
        elif (m := _DURATION.search(sentence)) and _months(*m.groups()) * 30 < MIN_RETURN_DAYS:
            found.append((Category.RETURNS, Severity.LOW, "Short return window"))
    if _RESTOCKING.search(sentence):
        found.append((Category.RETURNS, Severity.LOW, "Restocking fee on returns"))
    for pattern, category, severity, title in (
        (_SAFETY, Category.SAFETY, Severity.HIGH, "Safety concern reported"),
        (_RELIABILITY, Category.RELIABILITY, Severity.MEDIUM, "Reliability failure reported"),
        (_REPAIR, Category.REPAIRABILITY, Severity.LOW, "Limited repairability or upgrades"),
        (_SUPPORT, Category.SUPPORT, Severity.MEDIUM, "Software support may be limited"),
    ):
        match = pattern.search(sentence)
        if match and not _NEGATED.search(sentence[: match.start()]):
            found.append((category, severity, title))
    return found


def evidence_hits(evidence: dict[int, str]) -> list[Hit]:
    hits: list[Hit] = []
    for position, text in sorted(evidence.items()):
        for sentence in split_sentences(text):
            for category, severity, title in classify_sentence(sentence):
                hits.append(Hit(category, severity, title, position, sentence))
    return hits


def _merge(hits: Sequence[Hit]) -> list[Risk]:
    """One risk per (category, title); reliability escalates when several items report it."""
    grouped: dict[tuple[Category, str], list[Hit]] = {}
    for hit in hits:
        grouped.setdefault((hit.category, hit.title), []).append(hit)
    risks: list[Risk] = []
    for (category, title), items in grouped.items():
        positions = list(dict.fromkeys(h.position for h in items))
        severity = max(h.severity for h in items)
        if category is Category.RELIABILITY and len(positions) >= RELIABILITY_HIGH_ITEMS:
            severity = Severity.HIGH
        risks.append(
            Risk(
                category=category,
                severity=severity,
                title=title,
                citations=[f"E{p}" for p in positions[:3]],
                quote=items[0].quote,
                source="evidence",
            )
        )
    return risks


def agent_risks(
    spec: RequirementSpec | None,
    research: dict[str, Any] | None,
    compatibility: dict[str, Any] | None,
    value: dict[str, Any] | None,
) -> list[Risk]:
    risks: list[Risk] = []
    if compatibility:
        for check in compatibility.get("checks", []):
            if check.get("support") == "NOT_SUPPORTED":
                devices = ", ".join(check.get("devices", []))
                risks.append(
                    Risk(
                        category=Category.COMPATIBILITY,
                        severity=Severity.HIGH,
                        title=f"{check.get('label')} not supported (needed for {devices})",
                        source="compatibility",
                    )
                )
    if value and value.get("budget_fit") == "OVER":
        risks.append(
            Risk(
                category=Category.BUDGET,
                severity=Severity.MEDIUM,
                title=f"Over budget by {value.get('over_budget_by')}",
                source="value",
            )
        )
    if research and spec:
        must = {c.key for c in spec.criteria if c.priority is Priority.MUST}
        for fact in research.get("facts", []):
            if fact.get("key") not in must:
                continue
            if fact.get("status") == "UNMET":
                severity, title = Severity.HIGH, f"Required {fact.get('label')} not met"
            elif fact.get("status") in ("UNKNOWN", "NOT_COMPARABLE"):
                severity, title = Severity.MEDIUM, f"Required {fact.get('label')} unverified"
            else:
                continue
            risks.append(
                Risk(
                    category=Category.REQUIREMENTS,
                    severity=severity,
                    title=title,
                    source="product_research",
                )
            )
    return risks


def build_output(product: Product, hits: Sequence[Hit], others: Sequence[Risk]) -> RiskOutput:
    risks = sorted([*_merge(hits), *others], key=lambda r: (-r.severity, r.category.value, r.title))
    level = max((r.severity for r in risks), default=None)
    # Only evidence risks go into the cited summary; titles contain no digits.
    summary = " ".join(f"{r.title} [{', '.join(r.citations)}]." for r in risks if r.citations)
    return RiskOutput(
        product_id=str(product.id),
        level=level.name if level else "NONE",
        score=sum(r.severity for r in risks),
        summary=summary,
        risks=risks,
        checked_categories=[c.value for c in QUERIES],
    )
