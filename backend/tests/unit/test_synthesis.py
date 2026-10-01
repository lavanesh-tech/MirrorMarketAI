"""Synthesis Agent verdicts, scores and ranking (no database)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.agents.synthesis import Verdict, rank, synthesize
from app.domain.requirements import Criterion, Operator, Priority, RequirementSpec
from app.models.catalog import Product

pytestmark = pytest.mark.unit

SPEC = RequirementSpec(
    criteria=[
        Criterion(
            key="ram_gb", operator=Operator.GTE, value_number=Decimal(16), priority=Priority.MUST
        ),
        Criterion(key="weight_kg", operator=Operator.LTE, value_number=Decimal(2), weight=5),
    ]
)


def _p(name: str) -> Product:
    return Product(id=uuid.uuid4(), brand="Acme", name=name, category="laptop")


def _research(ram: str, weight: str = "MET") -> dict[str, Any]:
    return {
        "facts": [
            {"key": "ram_gb", "label": "Memory", "status": ram},
            {"key": "weight_kg", "label": "Weight", "status": weight},
        ]
    }


def test_recommended_with_full_score() -> None:
    out = synthesize(
        _p("A"),
        SPEC,
        {
            "product_research": _research("MET"),
            "review_intelligence": {
                "aspects": [{}],
                "overall": 1.0,
                "praises": ["battery"],
                "complaints": [],
            },
            "compatibility": {"verdict": "COMPATIBLE", "checks": []},
            "value": {"budget_fit": "WITHIN"},
            "risk": {"score": 0, "risks": []},
        },
        {"product_research": "r1"},
    )
    assert out.verdict is Verdict.RECOMMENDED
    assert out.score == 100.0
    assert out.requirement_fit == 1.0
    assert "value: within budget" in out.strengths
    assert "compatibility: works with all owned devices" in out.strengths
    assert out.summary == "Acme A: recommended."
    assert out.run_ids == {"product_research": "r1"}


def test_blockers_and_concerns() -> None:
    blocked = synthesize(
        _p("B"),
        SPEC,
        {
            "product_research": _research("UNMET"),
            "compatibility": {
                "verdict": "INCOMPATIBLE",
                "checks": [{"label": "HDMI", "support": "NOT_SUPPORTED"}],
            },
        },
        {},
    )
    assert blocked.verdict is Verdict.NOT_RECOMMENDED
    assert blocked.blockers == [
        "product_research: required Memory not met",
        "compatibility: not compatible (HDMI)",
    ]
    consider = synthesize(
        _p("C"),
        SPEC,
        {
            "product_research": _research("UNKNOWN"),
            "value": {"budget_fit": "OVER", "over_budget_by": "99.00"},
            "risk": {
                "score": 4,
                "risks": [
                    {"severity": "HIGH", "source": "evidence", "title": "Safety concern reported"}
                ],
            },
            "review_intelligence": {
                "aspects": [{}],
                "overall": -1.0,
                "praises": [],
                "complaints": ["keyboard"],
            },
        },
        {},
    )
    assert consider.verdict is Verdict.CONSIDER
    assert consider.concerns == [
        "product_research: required Memory unverified",
        "value: over budget by 99.00",
        "risk: Safety concern reported",
        "review_intelligence: reviewers criticise the keyboard",
    ]
    # fit 5/10 -> 30 points; reviews -1 -> 0; over budget -> 0; risk 4 -> -12
    assert consider.score == 18.0
    only_reviews = synthesize(
        _p("D"),
        SPEC,
        {
            "product_research": _research("MET"),
            "review_intelligence": {"complaints": ["fan"], "aspects": [{}], "overall": 0.0},
        },
        {},
    )
    assert only_reviews.verdict is Verdict.RECOMMENDED  # review complaints alone don't gate


def test_insufficient_data_and_no_spec() -> None:
    assert synthesize(_p("E"), SPEC, {}, {}).verdict is Verdict.INSUFFICIENT_DATA
    defaults = synthesize(
        _p("F"),
        None,
        {
            "product_research": {
                "facts": [
                    {"key": "a", "status": "NO_REQUIREMENT"},
                    {"key": "b", "status": "UNKNOWN"},
                ]
            }
        },
        {},
    )
    assert defaults.requirement_fit == 0.5
    assert (
        synthesize(_p("G"), None, {"product_research": {"facts": []}}, {}).requirement_fit is None
    )


def test_rank_gates_by_verdict_then_score() -> None:
    a = synthesize(_p("A"), SPEC, {"product_research": _research("MET", "UNMET")}, {})
    b = synthesize(_p("B"), SPEC, {"product_research": _research("MET")}, {})
    c = synthesize(_p("C"), SPEC, {"product_research": _research("UNMET")}, {})
    d = synthesize(_p("D"), SPEC, {}, {})
    assert [s.product_name for s in rank([d, c, a, b])] == ["Acme B", "Acme A", "Acme C", "Acme D"]
