"""Risk-detection benchmark for the rules engine of the Risk Agent.

Run: uv run python -m benchmarks.risk_detection
Synthetic, author-labelled sentences with expected (category, severity) pairs.
Written before running the classifier; nothing was tuned on it.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.risk import classify_sentence
from benchmarks.citations import _commit

CASES: list[tuple[str, set[tuple[str, str]]]] = [
    ("Acme covers the L14 with a one year limited warranty.", set()),
    ("Only a 90-day warranty is included.", {("warranty", "MEDIUM")}),
    ("The refurbished unit is sold as-is with no warranty.", {("warranty", "HIGH")}),
    ("Warranty: 6 months parts and labour.", {("warranty", "MEDIUM")}),
    ("Two-year warranty with accidental damage cover.", set()),
    ("Returns are accepted within 30 days for a full refund.", set()),
    ("All sales are final; no returns.", {("returns", "MEDIUM")}),
    ("Opened items carry a 15% restocking fee.", {("returns", "LOW")}),
    ("Refunds only within 7 days of delivery.", {("returns", "LOW")}),
    ("The charger was recalled in 2025 over fire risk.", {("safety", "HIGH")}),
    ("My battery started swelling after a year.", {("safety", "HIGH")}),
    ("It never overheats, even under load.", set()),
    ("Mine died after three weeks and support was slow.", {("reliability", "MEDIUM")}),
    ("No defects so far after six months of daily use.", set()),
    ("The hinge cracked within a month.", {("reliability", "MEDIUM")}),
    ("RAM is soldered and storage is not upgradeable.", {("repairability", "LOW")}),
    ("The battery is user replaceable with standard screws.", set()),
    ("This model is discontinued and gets no more updates.", {("support", "MEDIUM")}),
    ("Five years of OS updates are promised.", set()),
    ("Great screen, fast keyboard, quiet fans.", set()),
    ("It gets warm but nothing alarming.", set()),
    ("Several owners report dead pixels out of the box.", {("reliability", "MEDIUM")}),
]


def run() -> dict[str, object]:
    tp = fp = fn = exact = 0
    misses: list[str] = []
    for sentence, expected in CASES:
        found = {(c.value, s.name) for c, s, _ in classify_sentence(sentence)}
        tp += len(found & expected)
        fp += len(found - expected)
        fn += len(expected - found)
        if found == expected:
            exact += 1
        else:
            misses.append(f"{sentence!r}: got {sorted(found)}, want {sorted(expected)}")
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "benchmark": "risk_detection_rules",
        "dataset": f"synthetic, {len(CASES)} labelled sentences incl. negated and benign ones",
        "cases": len(CASES),
        "exact_match": round(exact / len(CASES), 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "misses": misses,
    }


def main() -> None:
    result = run() | {"commit": _commit()}
    out = Path(__file__).parent / "results" / "risk_detection.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
