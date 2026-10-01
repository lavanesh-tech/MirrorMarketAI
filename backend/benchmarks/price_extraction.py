"""Price-extraction benchmark for the Value Agent.

Run: uv run python -m benchmarks.price_extraction
Synthetic, author-labelled sentences: (text, expected amount or None, currency).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.agents.value import extract_price
from benchmarks.citations import _commit

CASES: list[tuple[str, Decimal | None, str | None]] = [
    ("The L14 retails for $1,499.99 in the US.", Decimal("1499.99"), "USD"),
    ("Was $1,999, now $1,499 at most stores.", Decimal(1499), "USD"),
    ("Weighs 1.4 kg. Starting at $999.", Decimal(999), "USD"),
    ("A 65W charger is included.", None, None),
    ("Grab it for £899 today.", Decimal(899), "GBP"),
    ("Price: €1.299 in Germany.", Decimal(1299), "EUR"),
    ("It costs $1,200 before tax ($1,299 with tax).", Decimal(1200), "USD"),
    ("The 32 GB model is priced at $1,799.", Decimal(1799), "USD"),
    ("Reduced from $649 to $549 for Black Friday.", Decimal(549), "USD"),
    ("MSRP $349; street price around $299.", Decimal(349), "USD"),
    ("Battery lasts 18 hours on a single charge.", None, None),
    ("Originally $2,499, the laptop now sells for $1,899.", Decimal(1899), "USD"),
    ("Accessories start at $29 and the laptop at $1,099.", Decimal(1099), "USD"),
    ("At $1299 it undercuts rivals.", Decimal(1299), "USD"),
]


def run() -> dict[str, object]:
    correct = 0
    misses: list[str] = []
    for text, amount, currency in CASES:
        found = extract_price(text)
        got = (found[0], found[1]) if found else (None, None)
        if got == (amount, currency):
            correct += 1
        else:
            misses.append(f"{text!r} -> {got}, want {(amount, currency)}")
    return {
        "benchmark": "price_extraction",
        "dataset": f"synthetic, {len(CASES)} labelled sentences (sale vs list prices, EU format)",
        "cases": len(CASES),
        "accuracy": round(correct / len(CASES), 4),
        "correct": correct,
        "misses": misses,
    }


def main() -> None:
    result = run() | {"commit": _commit()}
    out = Path(__file__).parent / "results" / "price_extraction.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
