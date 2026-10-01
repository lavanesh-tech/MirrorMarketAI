"""Aspect-sentiment benchmark for the rules engine of the Review Intelligence Agent.

Run: uv run python -m benchmarks.review_sentiment
Synthetic, author-labelled review sentences with expected (aspect, polarity) pairs,
including negation, contrast clauses, sarcasm and implicit opinions.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.review_intelligence import classify_sentence
from benchmarks.citations import _commit

P, N = "POSITIVE", "NEGATIVE"

CASES: list[tuple[str, set[tuple[str, str]]]] = [
    ("The battery easily lasts a full day.", {("battery", P)}),
    ("Battery life is not great.", {("battery", N)}),
    ("The screen is bright and the colors are vivid.", {("display", P)}),
    ("The display is dim outdoors.", {("display", N)}),
    ("Keyboard is mushy but the speakers are excellent.", {("keyboard", N), ("sound", P)}),
    ("The fans are not loud at all.", {("thermals", P)}),
    ("It runs hot under load and throttles quickly.", {("thermals", N)}),
    ("Overpriced for what you get.", {("value", N)}),
    ("Great value at this price.", {("value", P)}),
    ("Speakers sound tinny and the hinge feels flimsy.", {("sound", N), ("build", N)}),
    ("Build quality is superb, very sturdy chassis.", {("build", P)}),
    ("Never had any issues with reliability.", {("reliability", P)}),
    ("Mine broke after two weeks.", {("reliability", N)}),
    ("Noise cancellation is impressive on flights.", {("noise_cancellation", P)}),
    ("The ear cups are uncomfortable after an hour.", {("comfort", N)}),
    ("Bluetooth pairing is buggy.", {("connectivity", N)}),
    ("Performance is snappy for coding.", {("performance", P)}),
    ("The webcam is terrible in low light.", {("camera", N)}),
    ("Software updates arrive quickly and work well.", {("software", P)}),
    ("I would recommend it to anyone.", set()),
    ("It arrived on Tuesday in a white box.", set()),
    ("Oh great, another laptop whose battery dies by lunch.", {("battery", N)}),
    ("I barely notice the fan.", {("thermals", P)}),
    ("You will be charging this thing twice a day.", {("battery", N)}),
]


def run() -> dict[str, object]:
    tp = fp = fn = exact = 0
    misses: list[str] = []
    for sentence, expected in CASES:
        found = {(aspect, polarity.value) for aspect, polarity in classify_sentence(sentence)}
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
        "benchmark": "review_aspect_sentiment_rules",
        "dataset": f"synthetic, {len(CASES)} labelled review sentences (incl. sarcasm, implicit)",
        "cases": len(CASES),
        "exact_match": round(exact / len(CASES), 4),
        "pair_precision": round(precision, 4),
        "pair_recall": round(recall, 4),
        "pair_f1": round(2 * precision * recall / (precision + recall), 4)
        if precision + recall
        else 0.0,
        "misses": misses,
    }


def main() -> None:
    result = run() | {"commit": _commit()}
    out = Path(__file__).parent / "results" / "review_sentiment.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
