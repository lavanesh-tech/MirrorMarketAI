"""Risk Agent rules (no database)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.agents.risk import Category, Severity, agent_risks, build_output, evidence_hits
from app.domain.citations import validate_citations
from app.domain.requirements import Criterion, Operator, Priority, RequirementSpec
from app.models.catalog import Product
from benchmarks.risk_detection import run

pytestmark = pytest.mark.unit

PRODUCT = Product(id=uuid.uuid4(), brand="Acme", name="L14", category="laptop")


def test_evidence_risks_merge_and_escalate_reliability() -> None:
    evidence = {
        1: "Only a 90-day warranty is included. Mine died after a month.",
        2: "The hinge cracked within weeks.",
        3: "Great keyboard.",
    }
    out = build_output(PRODUCT, evidence_hits(evidence), [])
    by_category = {r.category: r for r in out.risks}
    assert by_category[Category.RELIABILITY].severity is Severity.HIGH  # two items report it
    assert by_category[Category.RELIABILITY].citations == ["E1", "E2"]
    assert by_category[Category.WARRANTY].severity is Severity.MEDIUM
    assert out.level == "HIGH"
    assert out.score == Severity.HIGH + Severity.MEDIUM
    assert out.summary == ("Reliability failure reported [E1, E2]. Short warranty coverage [E1].")
    assert validate_citations(out.summary, evidence).valid
    dumped = out.model_dump(mode="json")
    assert dumped["risks"][0]["severity"] == "HIGH"


def test_agent_risks_from_other_runs() -> None:
    spec = RequirementSpec(
        criteria=[
            Criterion(
                key="ram_gb",
                operator=Operator.GTE,
                value_number=Decimal(16),
                priority=Priority.MUST,
            ),
            Criterion(
                key="weight_kg",
                operator=Operator.LTE,
                value_number=Decimal(1),
                priority=Priority.MUST,
            ),
            Criterion(key="storage_gb", operator=Operator.GTE, value_number=Decimal(512)),
            Criterion(
                key="battery_wh",
                operator=Operator.GTE,
                value_number=Decimal(60),
                priority=Priority.MUST,
            ),
        ]
    )
    research = {
        "facts": [
            {"key": "ram_gb", "label": "Memory", "status": "MET"},
            {"key": "weight_kg", "label": "Weight", "status": "UNMET"},
            {"key": "storage_gb", "label": "Storage", "status": "UNMET"},
            {"key": "battery_wh", "label": "Battery capacity", "status": "UNKNOWN"},
        ]
    }
    compatibility = {
        "checks": [
            {"label": "HDMI", "support": "NOT_SUPPORTED", "devices": ["Sony TV"]},
            {"label": "USB-C", "support": "SUPPORTED", "devices": ["dock"]},
        ]
    }
    value = {"budget_fit": "OVER", "over_budget_by": "99.00"}
    risks = agent_risks(spec, research, compatibility, value)
    assert [(r.category, r.severity, r.title) for r in risks] == [
        (Category.COMPATIBILITY, Severity.HIGH, "HDMI not supported (needed for Sony TV)"),
        (Category.BUDGET, Severity.MEDIUM, "Over budget by 99.00"),
        (Category.REQUIREMENTS, Severity.HIGH, "Required Weight not met"),
        (Category.REQUIREMENTS, Severity.MEDIUM, "Required Battery capacity unverified"),
    ]
    out = build_output(PRODUCT, [], risks)
    assert (out.level, out.summary) == ("HIGH", "")  # uncited risks stay out of the summary
    assert agent_risks(None, research, None, {"budget_fit": "WITHIN"}) == []


def test_no_risks() -> None:
    out = build_output(PRODUCT, evidence_hits({1: "Two-year warranty. Solid build."}), [])
    assert (out.level, out.score, out.risks) == ("NONE", 0, [])


def test_risk_benchmark() -> None:
    result = run()
    assert result["cases"] == 22
    assert (result["precision"], result["recall"]) == (1.0, 1.0)
