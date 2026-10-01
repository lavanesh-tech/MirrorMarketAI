"""Extractive QA benchmark: does the answer cite the right evidence, and abstain correctly?

Run: uv run python -m benchmarks.qa_extractive
Synthetic, author-labelled questions over a fixed evidence pack. `expected` is the
item that must be cited first, or None when the question is unanswerable.
Written before measuring; MIN_COVERAGE was not tuned on it.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.answering import MIN_COVERAGE, extractive_answer
from benchmarks.citations import _commit

EVIDENCE = {
    1: "The Acme L14 battery is rated 70 Wh and lasts up to 18 hours of video playback.",
    2: "Memory on the Acme L14 is 16 GB LPDDR5, soldered to the board and not upgradeable.",
    3: "Ports include two Thunderbolt 4 USB-C connectors, HDMI 2.1 and a headphone jack.",
    4: "Acme covers the L14 with a one year limited hardware warranty against defects.",
    5: "The 14 inch OLED display has a 120 Hz refresh rate and 500 nits peak brightness.",
    6: "The L14 weighs 1.24 kg and is 15 mm thick.",
    7: "Reviewers praise the keyboard but say the speakers sound thin.",
}

CASES: list[tuple[str, int | None]] = [
    ("How long does the battery last?", 1),
    ("What is the battery capacity in Wh?", 1),
    ("How much memory does it have?", 2),
    ("Can I upgrade the memory later?", 2),
    ("Does it have an HDMI port?", 3),
    ("Which ports are included?", 3),
    ("How long is the warranty?", 4),
    ("What refresh rate does the display have?", 5),
    ("How bright is the OLED screen?", 5),
    ("How much does the L14 weigh?", 6),
    ("How thick is it?", 6),
    ("What do reviewers say about the keyboard?", 7),
    ("Are the speakers good?", 7),
    ("Does it support Wi-Fi 7?", None),
    ("What colours are available?", None),
    ("Is there a fingerprint reader?", None),
    ("How much does shipping cost to Canada?", None),
    ("Which GPU does it use?", None),
]

# Written after the stopword/suffix changes; never used for tuning.
HELD_OUT_EVIDENCE = {
    1: "The Sono H9 headphones use hybrid active noise cancellation with eight microphones.",
    2: "Bluetooth 5.3 on the Sono H9 supports the LDAC and AAC audio codecs.",
    3: "The Sono H9 weighs 250 grams and folds flat into the included travel case.",
    4: "Battery life is rated at 30 hours with noise cancellation turned on.",
    5: "A 10 minute charge over USB-C adds about 5 hours of playback.",
    6: "Several reviewers report the ear cushions get warm during long sessions.",
}
HELD_OUT: list[tuple[str, int | None]] = [
    ("Does it have noise cancellation?", 1),
    ("Which codecs are supported?", 2),
    ("Is LDAC available?", 2),
    ("What does it weigh?", 3),
    ("Does it come with a case?", 3),
    ("What is the battery life?", 4),
    ("How fast does it charge?", 5),
    ("Do the ear cushions get hot?", 6),
    ("Is it water resistant?", None),
    ("Does it have a microphone mute button?", None),
    ("What is the price in Europe?", None),
]


def _score(
    cases: list[tuple[str, int | None]], evidence: dict[int, str]
) -> tuple[int, int, int, int, list[str]]:
    correct = answered_right = abstain_right = 0
    misses: list[str] = []
    for question, expected in cases:
        answer = extractive_answer(question, evidence)
        if expected is None:
            ok = answer.abstained
            abstain_right += ok
        else:
            ok = not answer.abstained and answer.cited[:1] == [expected]
            answered_right += ok
        correct += ok
        if not ok:
            misses.append(
                f"{question!r}: cited {answer.cited} (coverage {answer.coverage}), want {expected}"
            )
    answerable = sum(1 for _, e in cases if e is not None)
    return correct, answered_right, abstain_right, answerable, misses


def run() -> dict[str, object]:
    correct, answered_right, abstain_right, answerable, misses = _score(CASES, EVIDENCE)
    held = _score(HELD_OUT, HELD_OUT_EVIDENCE)
    return {
        "benchmark": "qa_extractive",
        "dataset": f"synthetic, {len(CASES)} questions ({answerable} answerable) over 7 items",
        "min_coverage": MIN_COVERAGE,
        "accuracy": round(correct / len(CASES), 4),
        "answerable_accuracy": round(answered_right / answerable, 4),
        "abstention_accuracy": round(abstain_right / (len(CASES) - answerable), 4),
        "misses": misses,
        "note": "tuned on `cases` (accuracy 0.7222 before stopword/suffix changes)",
        "held_out_cases": len(HELD_OUT),
        "held_out_accuracy": round(held[0] / len(HELD_OUT), 4),
        "held_out_misses": held[4],
    }


def main() -> None:
    result = run() | {"commit": _commit()}
    out = Path(__file__).parent / "results" / "qa_extractive.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
