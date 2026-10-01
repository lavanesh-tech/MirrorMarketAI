"""Fact-extraction benchmark for the rules engine of the Product Research Agent.

Run: uv run python -m benchmarks.fact_extraction
Synthetic, author-labelled spec-sheet sentences, including phrasings the rules are
expected to miss, so the number reflects real coverage of the patterns.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.agents.product_research import extract_value
from benchmarks.citations import _commit

# (evidence text, key, expected number or text; None = nothing should be extracted)
CASES: list[tuple[str, str, Decimal | str | None]] = [
    ("Memory: 16 GB LPDDR5x, soldered.", "ram_gb", Decimal(16)),
    ("Comes with 32GB of unified memory.", "ram_gb", Decimal(32)),
    ("RAM 8 GB (expandable to 64 GB).", "ram_gb", Decimal(8)),
    ("Storage: 1TB PCIe 4.0 SSD.", "storage_gb", Decimal(1024)),
    ("512 GB NVMe SSD storage.", "storage_gb", Decimal(512)),
    ("Up to 18 hours of battery life.", "battery_life_hours", Decimal(18)),
    ("Battery: up to 22 hrs video playback.", "battery_life_hours", Decimal(22)),
    ("Battery lasts all day.", "battery_life_hours", None),
    ("Weight: 1.24 kg (2.73 lbs).", "weight_kg", Decimal("1.24")),
    ("Starting at 2.7 pounds.", "weight_kg", Decimal("1.225")),
    ("The headphones weigh 250 g.", "weight_kg", Decimal("0.25")),
    ("14.2-inch Liquid Retina display.", "screen_size_in", Decimal("14.2")),
    ('Display: 13.6" IPS panel.', "screen_size_in", Decimal("13.6")),
    ("120Hz ProMotion refresh rate.", "refresh_rate_hz", Decimal(120)),
    ("Peak brightness of 1600 nits (HDR).", "brightness_nits", Decimal(1600)),
    ("72.4 Wh lithium-polymer battery.", "battery_wh", Decimal("72.4")),
    ("A 5,000 mAh battery.", "battery_mah", Decimal(5000)),
    ("Two Thunderbolt 4 ports.", "has_thunderbolt", "yes"),
    ("No touchscreen on this model.", "has_touchscreen", "no"),
    ("Active noise cancellation with 8 mics.", "has_noise_cancellation", "yes"),
    ("Sixteen gigabytes of memory.", "ram_gb", Decimal(16)),
    ("Half a terabyte of storage.", "storage_gb", Decimal(512)),
    ("Weighs one and a half kilograms.", "weight_kg", Decimal("1.5")),
    ("The 15-inch model has a bigger screen.", "screen_size_in", Decimal(15)),
]


def run() -> dict[str, object]:
    correct = 0
    misses: list[str] = []
    for text, key, expected in CASES:
        found = extract_value(key, text)
        value: Decimal | str | None = None
        if found is not None:
            number, label, _unit = found
            value = number if number is not None else label
        ok = value == expected
        correct += ok
        if not ok:
            misses.append(f"{key}: {text!r} -> {value}")
    return {
        "benchmark": "fact_extraction_rules",
        "dataset": f"synthetic, {len(CASES)} labelled spec sentences (incl. spelled-out numbers)",
        "cases": len(CASES),
        "accuracy": round(correct / len(CASES), 4),
        "correct": correct,
        "misses": misses,
    }


def main() -> None:
    result = run() | {"commit": _commit()}
    out = Path(__file__).parent / "results" / "fact_extraction.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
