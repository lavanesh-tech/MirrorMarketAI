"""Citation-validator benchmark on a labelled synthetic set.

Run: uv run python -m benchmarks.citations
Writes benchmarks/results/citations.json. Each case lists the issue codes a
correct validator must report (empty = the answer is correctly cited).
Metrics: case accuracy (valid vs invalid), and issue-level precision / recall.
"""

from __future__ import annotations

import json
import platform
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.domain.citations import validate_citations

EVIDENCE = {
    1: "The Acme L14 battery is rated 70 Wh and lasts up to 18 hours of video playback.",
    2: "Memory on the Acme L14 is 16 GB LPDDR5, soldered to the board and not upgradeable.",
    3: "Ports include two Thunderbolt 4 USB-C connectors, HDMI 2.1 and a headphone jack.",
    4: "Acme covers the L14 with a one year limited hardware warranty against defects.",
    5: "The Sono H9 weighs 250 grams and supports LDAC and AAC over Bluetooth 5.3.",
}

# (answer, expected issue codes)
CASES: list[tuple[str, list[str]]] = [
    ("The L14 lasts up to 18 hours of video playback [E1].", []),
    ("Its battery is rated 70 Wh [E1] and memory is 16 GB [E2].", []),
    ("Memory is soldered and cannot be upgraded later [E2].", []),
    ('Acme says memory is "soldered to the board" [E2].', []),
    ("It has two Thunderbolt 4 ports plus HDMI 2.1 [E3].", []),
    ("The warranty is a one year limited hardware warranty [E4].", []),
    ("The Sono H9 weighs 250 grams [E5]. It supports LDAC over Bluetooth 5.3 [E5].", []),
    ("Battery is 70 Wh with 16 GB of memory [E1, E2].", []),
    ("Great choice. The L14 offers long battery life for travel [E1].", []),
    ("The L14 lasts up to 20 hours of video playback [E1].", ["unsupported_number"]),
    ("Memory is 32 GB and not upgradeable [E2].", ["unsupported_number"]),
    ("The battery is rated 80 Wh [E1].", ["unsupported_number"]),
    ("The Sono H9 weighs 200 grams [E5].", ["unsupported_number"]),
    ("It includes three Thunderbolt 5 ports [E3].", ["unsupported_number"]),
    ('Acme calls the memory "easily upgradeable" [E2].', ["unsupported_quote"]),
    ('The warranty is "three years, no questions asked" [E4].', ["unsupported_quote"]),
    ('Reviewers say the H9 "sounds amazing" [E5].', ["unsupported_quote"]),
    ("The L14 has a one year warranty [E6].", ["unknown_citation"]),
    ("Ports include HDMI 2.1 [E9].", ["unknown_citation"]),
    ("Memory is 16 GB [E2, E7].", ["unknown_citation"]),
    ("The L14 is the best laptop for students on a budget.", ["uncited_sentence"]),
    (
        "Battery lasts 18 hours [E1]. The keyboard is comfortable for long typing sessions.",
        ["uncited_sentence"],
    ),
    ("It is lighter than most competitors in this price range.", ["uncited_sentence"]),
    (
        "Battery lasts 24 hours [E1]. It is the fastest laptop available today.",
        ["unsupported_number", "uncited_sentence"],
    ),
]


def run() -> dict[str, object]:
    expected_total: Counter[str] = Counter()
    found_total: Counter[str] = Counter()
    true_positive: Counter[str] = Counter()
    correct_cases = 0
    for answer, expected in CASES:
        report = validate_citations(answer, EVIDENCE)
        found = Counter(issue.code.value for issue in report.issues)
        wanted = Counter(expected)
        expected_total += wanted
        found_total += found
        true_positive += found & wanted
        correct_cases += report.valid == (not expected)
    tp, fp, fn = (
        sum(true_positive.values()),
        sum((found_total - true_positive).values()),
        sum((expected_total - true_positive).values()),
    )
    return {
        "benchmark": "citation_validator",
        "dataset": "synthetic, 24 labelled answers over 5 evidence items",
        "cases": len(CASES),
        "case_accuracy": round(correct_cases / len(CASES), 4),
        "issue_precision": round(tp / (tp + fp), 4) if tp + fp else 1.0,
        "issue_recall": round(tp / (tp + fn), 4) if tp + fn else 1.0,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
    }


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> None:
    result = run() | {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": _commit(),
        "python": platform.python_version(),
        "machine": platform.machine(),
    }
    out = Path(__file__).parent / "results" / "citations.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
