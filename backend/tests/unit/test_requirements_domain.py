"""RequirementSpec validation, diffing and the offline rule extractor."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.requirements import (
    Budget,
    Criterion,
    Operator,
    Priority,
    RequirementSpec,
    diff_specs,
)
from app.extraction.rules import extract_requirements

pytestmark = pytest.mark.unit


def _c(
    key: str, op: str, number: str | None = None, text: str | None = None, **kw: object
) -> Criterion:
    return Criterion(
        key=key,
        operator=Operator(op),
        value_number=Decimal(number) if number is not None else None,
        value_text=text,
        **kw,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ validation
def test_criterion_requires_exactly_one_value_and_numeric_ranges() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        Criterion(key="ram_gb", operator=Operator.GTE)
    with pytest.raises(ValidationError, match="exactly one"):
        _c("ram_gb", ">=", "16", "sixteen")
    with pytest.raises(ValidationError, match="numeric"):
        _c("color", ">=", text="red")
    with pytest.raises(ValidationError, match="snake_case"):
        _c("RAM GB", ">=", "16")
    with pytest.raises(ValidationError):
        _c("ram_gb", ">=", "16", weight=6)
    ok = _c("color", "!=", text="pink", priority=Priority.MUST)
    assert ok.priority is Priority.MUST


def test_budget_rules() -> None:
    with pytest.raises(ValidationError, match="min_amount or max_amount"):
        Budget()
    with pytest.raises(ValidationError, match="exceed"):
        Budget(min_amount=Decimal(500), max_amount=Decimal(100))
    with pytest.raises(ValidationError):
        Budget(max_amount=Decimal(100), currency="usd")
    assert Budget(max_amount=Decimal("999.99")).currency == "USD"


def test_spec_normalizes_lists_and_rejects_duplicates_and_unknown_category() -> None:
    spec = RequirementSpec(
        excluded_brands=[" Dell ", "dell", "HP", ""], use_cases=["travel", "Travel"]
    )
    assert spec.excluded_brands == ["Dell", "HP"]
    assert spec.use_cases == ["travel"]
    with pytest.raises(ValidationError, match="duplicate criterion"):
        RequirementSpec(criteria=[_c("ram_gb", ">=", "8"), _c("ram_gb", ">=", "16")])
    with pytest.raises(ValidationError, match="category"):
        RequirementSpec(category="spaceship")
    with pytest.raises(ValidationError):
        RequirementSpec.model_validate({"unknown_field": 1})
    # Same key with different operators is a range, which is allowed.
    ranged = RequirementSpec(
        criteria=[
            _c("screen_size_in", ">=", "13"),
            _c("screen_size_in", "<=", "15", priority="MUST"),
        ]
    )
    assert [c.key for c in ranged.hard_constraints] == ["screen_size_in"]


def test_to_json_round_trips_without_precision_loss() -> None:
    spec = RequirementSpec(
        budget=Budget(max_amount=Decimal("1499.99")), criteria=[_c("weight_kg", "<=", "1.234567")]
    )
    data = spec.to_json()
    assert data["budget"]["max_amount"] == "1499.99"
    assert RequirementSpec.model_validate(data) == spec


def test_diff_specs() -> None:
    before = RequirementSpec(
        category="laptop",
        budget=Budget(max_amount=Decimal(1500)),
        criteria=[_c("ram_gb", ">=", "16"), _c("weight_kg", "<=", "1.5")],
        excluded_brands=["Dell"],
    )
    after = RequirementSpec(
        category="laptop",
        budget=Budget(max_amount=Decimal(1800)),
        criteria=[
            _c("ram_gb", ">=", "32", priority="MUST"),
            _c("has_oled_display", "=", text="yes"),
        ],
        excluded_brands=["Dell"],
    )
    diff = diff_specs(before, after)
    assert [c.field for c in diff.changes] == ["budget"]
    assert diff.changes[0].after["max_amount"] == "1800"
    assert [c.key for c in diff.criteria_added] == ["has_oled_display"]
    assert [c.key for c in diff.criteria_removed] == ["weight_kg"]
    assert diff.criteria_changed[0].field == "ram_gb >="
    assert diff.criteria_changed[0].after["value_number"] == "32"
    assert not diff.is_empty
    assert diff_specs(before, before).is_empty


# ------------------------------------------------------------ rule extraction
def _by_key(spec: RequirementSpec) -> dict[str, Criterion]:
    return {c.key: c for c in spec.criteria}


def test_extracts_a_typical_laptop_brief() -> None:
    result = extract_requirements(
        "Need a laptop under $1,500 for programming and travel. Must have at least 16 GB RAM, "
        "ideally under 1.4 kg. No touchscreen. Avoid Dell."
    )
    spec = result.spec
    assert spec.category == "laptop"
    assert spec.budget == Budget(max_amount=Decimal(1500), currency="USD")
    assert spec.use_cases == ["programming", "travel"]
    assert spec.excluded_brands == ["Dell"]
    found = _by_key(spec)
    assert found["ram_gb"] == _c("ram_gb", ">=", "16", unit="GB", priority=Priority.MUST)
    assert found["weight_kg"] == _c("weight_kg", "<=", "1.4", unit="kg", priority=Priority.SHOULD)
    assert found["has_touchscreen"] == _c("has_touchscreen", "=", text="no", priority=Priority.MUST)
    assert result.unparsed == []


def test_extracts_ranges_units_and_conversions() -> None:
    result = extract_requirements(
        "Looking for noise cancelling headphones between $200 and $350, battery 30 hours or "
        "more. Lighter than 250 g please."
    )
    spec = result.spec
    assert spec.category == "headphones"
    assert spec.budget == Budget(min_amount=Decimal(200), max_amount=Decimal(350))
    found = _by_key(spec)
    assert found["battery_life_hours"].value_number == Decimal(30)
    assert found["battery_life_hours"].operator is Operator.GTE
    assert found["weight_kg"].value_number == Decimal("0.25")
    assert found["has_noise_cancellation"].value_text == "yes"


@pytest.mark.parametrize(
    ("text", "key", "operator", "value"),
    [
        ("phone with 1TB storage", "storage_gb", ">=", "1024"),
        ("at least 512 GB SSD", "storage_gb", ">=", "512"),
        ("27 inch monitor", "screen_size_in", ">=", "27"),
        ("144Hz or more", "refresh_rate_hz", ">=", "144"),
        ("at least 400 nits", "brightness_nits", ">=", "400"),
        ("under 3 lbs", "weight_kg", "<=", "1.361"),
        ("5000 mAh battery", "battery_mah", ">=", "5000"),
        ("a 70 Wh battery", "battery_wh", ">=", "70"),
        ("max 8 GB of RAM", "ram_gb", "<=", "8"),
    ],
)
def test_quantity_patterns(text: str, key: str, operator: str, value: str) -> None:
    found = _by_key(extract_requirements(text).spec)
    assert found[key].operator == Operator(operator)
    assert found[key].value_number == Decimal(value)


@pytest.mark.parametrize(
    ("text", "min_amount", "max_amount", "currency"),
    [
        ("budget around $1.2k", None, "1200", "USD"),
        ("€800 - €1,000", "800", "1000", "EUR"),
        ("spending £600", None, "600", "GBP"),
        ("it costs $450 roughly", None, "450", "USD"),
        ("$2k to $3k", "2000", "3000", "USD"),
    ],
)
def test_budget_patterns(text: str, min_amount: str | None, max_amount: str, currency: str) -> None:
    budget = extract_requirements(text).spec.budget
    assert budget is not None
    assert budget.min_amount == (Decimal(min_amount) if min_amount else None)
    assert budget.max_amount == Decimal(max_amount)
    assert budget.currency == currency


def test_preferences_brands_and_unparsed_text() -> None:
    result = extract_requirements(
        "A tablet, wireless charging preferred, not from Samsung. I also like cats."
    )
    spec = result.spec
    assert spec.category == "tablet"
    assert spec.excluded_brands == ["Samsung"]
    assert _by_key(spec)["has_wireless_charging"].priority is Priority.SHOULD
    assert result.unparsed == ["I also like cats."]


def test_negated_feature_words_are_not_brands_and_empty_text_is_safe() -> None:
    result = extract_requirements("No Thunderbolt needed. No OLED.")
    assert result.spec.excluded_brands == []
    assert _by_key(result.spec)["has_thunderbolt"].value_text == "no"
    assert _by_key(result.spec)["has_oled_display"].value_text == "no"
    empty = extract_requirements("   ")
    assert empty.spec == RequirementSpec()
    assert empty.unparsed == []
