"""Compatibility Agent building blocks (no database)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.agents.compatibility import (
    Finding,
    Requirement,
    Support,
    Verdict,
    build_output,
    detect,
    parse_llm_findings,
    required_capabilities,
)
from app.domain.citations import validate_citations
from app.extraction.rules import extract_requirements
from app.models.catalog import Product, ProductSpecification
from app.providers.llm import JsonCompletion
from benchmarks.compatibility import run

pytestmark = pytest.mark.unit


def _product(category: str = "laptop") -> Product:
    return Product(id=uuid.uuid4(), brand="Acme", name="L14", category=category)


def test_required_capabilities() -> None:
    reqs = required_capabilities(["iPhone 15", "USB-C dock", "Sony TV", "my cat"], "laptop")
    assert [(r.capability, r.devices) for r in reqs] == [
        ("ios", ("iPhone 15",)),
        ("usb_c", ("USB-C dock",)),
        ("hdmi", ("Sony TV",)),
    ]
    phones = required_capabilities(["iPhone 15", "Pixel 8"], "headphones")
    assert [r.capability for r in phones] == ["ios", "bluetooth", "aac", "android"]
    assert next(r for r in phones if r.capability == "bluetooth").devices == (
        "iPhone 15",
        "Pixel 8",
    )


def test_owned_devices_are_extracted_from_briefs() -> None:
    spec = extract_requirements(
        "I have an iPhone 15 and a USB-C dock. Must work with my Sony TV."
    ).spec
    assert spec.owned_devices == ["iPhone 15", "USB-C dock", "Sony TV"]


def test_detect() -> None:
    assert detect("hdmi", "Battery is fine. No HDMI here.") == (
        Support.NOT_SUPPORTED,
        "No HDMI here.",
    )
    assert detect("usb_c", "Charges over USB-C.") == (Support.SUPPORTED, "Charges over USB-C.")
    assert detect("ethernet", "Nothing relevant.") is None


def test_build_output_verdicts_and_catalog_fallback() -> None:
    reqs = [
        Requirement("usb_c", ("USB-C dock",)),
        Requirement("hdmi", ("Sony TV",)),
        Requirement("sd_card", ("camera SD card",)),
    ]
    evidence = {1: "Charges over USB-C.", 2: "There is no HDMI port."}
    findings = {
        "usb_c": Finding(Support.SUPPORTED, 1, "Charges over USB-C."),
        "hdmi": Finding(Support.NOT_SUPPORTED, 2, "There is no HDMI port."),
    }
    catalog = [ProductSpecification(key="has_sd_card_reader", value_text="yes")]
    out = build_output(
        _product(), ["USB-C dock", "Sony TV", "camera SD card", "a cat"], reqs, findings, catalog
    )
    assert out.verdict is Verdict.INCOMPATIBLE
    assert [c.support for c in out.checks] == [
        Support.SUPPORTED,
        Support.NOT_SUPPORTED,
        Support.SUPPORTED,
    ]
    assert out.checks[2].source == "catalog"
    assert out.unmapped_devices == ["a cat"]
    assert out.summary == "USB-C: supported [E1]. HDMI: not supported [E2]."
    assert validate_citations(out.summary, evidence).valid

    ok = build_output(_product(), ["USB-C dock"], reqs[:1], findings, [])
    assert ok.verdict is Verdict.COMPATIBLE
    unknown = build_output(
        _product(),
        ["x"],
        [Requirement("ethernet", ("x",))],
        {},
        [ProductSpecification(key="ram_gb", value_number=Decimal(16))],
    )
    assert (unknown.verdict, unknown.checks[0].support) == (Verdict.UNCERTAIN, Support.UNKNOWN)
    assert build_output(_product(), [], [], {}, []).verdict is Verdict.UNCERTAIN


def test_parse_llm_findings_guards() -> None:
    evidence = {1: "No HDMI port. Charges via USB-C.", 2: "Bluetooth 5.3."}
    completion = JsonCompletion(
        {
            "checks": [
                {
                    "capability": "hdmi",
                    "support": "NOT_SUPPORTED",
                    "marker": "E1",
                    "quote": "No HDMI port.",
                },
                {
                    "capability": "hdmi",
                    "support": "SUPPORTED",
                    "marker": "E1",
                    "quote": "No HDMI port.",
                },
                {
                    "capability": "usb_c",
                    "support": "SUPPORTED",
                    "marker": "E2",
                    "quote": "Charges via USB-C.",
                },
                {
                    "capability": "bluetooth",
                    "support": "SUPPORTED",
                    "marker": "E2",
                    "quote": "Bluetooth 6.0.",
                },
                {
                    "capability": "wifi",
                    "support": "SUPPORTED",
                    "marker": "E2",
                    "quote": "Bluetooth 5.3.",
                },
                {
                    "capability": "ethernet",
                    "support": "SUPPORTED",
                    "marker": "X1",
                    "quote": "No HDMI port.",
                },
                {"capability": "ethernet", "support": "SUPPORTED", "marker": "E1", "quote": "No"},
                7,
            ]
        },
        tokens_used=1,
    )
    found = parse_llm_findings(completion, ["hdmi", "usb_c", "bluetooth", "ethernet"], evidence)
    assert found == {"hdmi": Finding(Support.NOT_SUPPORTED, 1, "No HDMI port.")}


def test_compatibility_benchmark() -> None:
    result = run()
    assert result["accuracy"] == 1.0
    assert result["held_out_accuracy"] == 1.0
