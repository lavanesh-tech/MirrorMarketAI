"""Synthesis Agent: one verdict per product from the other agents' stored outputs.

Deterministic and explainable. Every reason names the agent it came from:
- NOT_RECOMMENDED when a hard constraint fails (a MUST criterion UNMET, or the
  product INCOMPATIBLE with owned devices);
- CONSIDER when there is a HIGH risk, it is over budget, or a MUST is unverified;
- RECOMMENDED otherwise (at least the research step must have succeeded).
Score (0-100) = 60 x requirement fit + 20 x review sentiment + 20 x budget fit
- 3 per risk severity point (capped at 30). It ranks products; the verdict gates them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from app.domain.requirements import Priority, RequirementSpec
from app.models.catalog import Product

MUST_WEIGHT = 5
RISK_POINT_PENALTY = 3
MAX_RISK_PENALTY = 30
BUDGET_POINTS = {"WITHIN": 20.0, "NO_BUDGET": 10.0, "UNKNOWN_PRICE": 10.0, "UNDER_MIN": 15.0}


class Verdict(StrEnum):
    RECOMMENDED = "RECOMMENDED"
    CONSIDER = "CONSIDER"
    NOT_RECOMMENDED = "NOT_RECOMMENDED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ProductSynthesis(BaseModel):
    product_id: str
    product_name: str
    verdict: Verdict
    score: float
    requirement_fit: float | None
    blockers: list[str]
    concerns: list[str]
    strengths: list[str]
    run_ids: dict[str, str]
    summary: str


def _requirement_fit(spec: RequirementSpec | None, research: dict[str, Any] | None) -> float | None:
    if research is None:
        return None
    facts = research.get("facts", [])
    if spec is None or not spec.criteria:
        known = [f for f in facts if f.get("status") != "UNKNOWN"]
        return round(len(known) / len(facts), 4) if facts else None
    status = {f["key"]: f.get("status") for f in facts}
    total = met = 0
    for criterion in spec.criteria:
        weight = MUST_WEIGHT if criterion.priority is Priority.MUST else criterion.weight
        total += weight
        met += weight if status.get(criterion.key) == "MET" else 0
    return round(met / total, 4)


@dataclass(slots=True)
class _Findings:
    blockers: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)


def _from_research(found: _Findings, research: dict[str, Any] | None, must: set[str]) -> None:
    for fact in (research or {}).get("facts", []):
        label, status, required = fact.get("label"), fact.get("status"), fact.get("key") in must
        if required and status == "UNMET":
            found.blockers.append(f"product_research: required {label} not met")
        elif required and status in ("UNKNOWN", "NOT_COMPARABLE"):
            found.concerns.append(f"product_research: required {label} unverified")
        elif status == "MET":
            found.strengths.append(f"product_research: {label} meets the requirement")


def _from_compatibility(found: _Findings, compatibility: dict[str, Any] | None) -> None:
    verdict = (compatibility or {}).get("verdict")
    if verdict == "INCOMPATIBLE":
        failed = [
            c["label"]
            for c in (compatibility or {}).get("checks", [])
            if c.get("support") == "NOT_SUPPORTED"
        ]
        found.blockers.append(f"compatibility: not compatible ({', '.join(failed)})")
    elif verdict == "COMPATIBLE":
        found.strengths.append("compatibility: works with all owned devices")


def _from_value_risk_reviews(
    found: _Findings,
    value: dict[str, Any] | None,
    risk: dict[str, Any] | None,
    reviews: dict[str, Any] | None,
) -> None:
    fit = (value or {}).get("budget_fit")
    if fit == "OVER":
        found.concerns.append(f"value: over budget by {(value or {}).get('over_budget_by')}")
    elif fit == "WITHIN":
        found.strengths.append("value: within budget")
    for item in (risk or {}).get("risks", []):
        if item.get("severity") == "HIGH" and item.get("source") == "evidence":
            found.concerns.append(f"risk: {item.get('title')}")
    for aspect in (reviews or {}).get("complaints", []):
        found.concerns.append(
            f"review_intelligence: reviewers criticise the {aspect.replace('_', ' ')}"
        )
    for aspect in (reviews or {}).get("praises", [])[:3]:
        found.strengths.append(
            f"review_intelligence: reviewers praise the {aspect.replace('_', ' ')}"
        )


def _verdict(research: dict[str, Any] | None, found: _Findings) -> Verdict:
    if research is None:
        return Verdict.INSUFFICIENT_DATA
    if found.blockers:
        return Verdict.NOT_RECOMMENDED
    if any(not c.startswith("review_intelligence") for c in found.concerns):
        return Verdict.CONSIDER
    return Verdict.RECOMMENDED


def _score(
    requirement_fit: float | None,
    reviews: dict[str, Any] | None,
    value: dict[str, Any] | None,
    risk: dict[str, Any] | None,
) -> float:
    review_points = 10.0
    if reviews and reviews.get("aspects"):
        review_points = (float(reviews.get("overall", 0.0)) + 1) / 2 * 20
    budget_points = BUDGET_POINTS.get((value or {}).get("budget_fit") or "", 0.0)
    risk_penalty = min(MAX_RISK_PENALTY, RISK_POINT_PENALTY * int((risk or {}).get("score", 0)))
    score = 60 * (requirement_fit or 0.0) + review_points + budget_points - risk_penalty
    return round(max(0.0, min(100.0, score)), 1)


def synthesize(
    product: Product,
    spec: RequirementSpec | None,
    outputs: dict[str, dict[str, Any]],
    run_ids: dict[str, str],
) -> ProductSynthesis:
    research = outputs.get("product_research")
    reviews = outputs.get("review_intelligence")
    value = outputs.get("value")
    risk = outputs.get("risk")
    must = {c.key for c in spec.criteria if c.priority is Priority.MUST} if spec else set()

    found = _Findings()
    _from_research(found, research, must)
    _from_compatibility(found, outputs.get("compatibility"))
    _from_value_risk_reviews(found, value, risk, reviews)

    requirement_fit = _requirement_fit(spec, research)
    verdict = _verdict(research, found)
    name = f"{product.brand} {product.name}"
    return ProductSynthesis(
        product_id=str(product.id),
        product_name=name,
        verdict=verdict,
        score=_score(requirement_fit, reviews, value, risk),
        requirement_fit=requirement_fit,
        blockers=found.blockers,
        concerns=found.concerns,
        strengths=found.strengths,
        run_ids=run_ids,
        summary=f"{name}: {verdict.value.replace('_', ' ').lower()}.",
    )


def rank(syntheses: list[ProductSynthesis]) -> list[ProductSynthesis]:
    """Verdict gates first (recommended > consider > others), then score, then name."""
    order = {
        Verdict.RECOMMENDED: 0,
        Verdict.CONSIDER: 1,
        Verdict.NOT_RECOMMENDED: 2,
        Verdict.INSUFFICIENT_DATA: 3,
    }
    return sorted(syntheses, key=lambda s: (order[s.verdict], -s.score, s.product_name))
