"""Citation validator rules and the labelled benchmark."""

from __future__ import annotations

import pytest

from app.domain.citations import IssueCode, split_sentences, validate_citations
from benchmarks.citations import run

pytestmark = pytest.mark.unit

EVIDENCE = {
    1: "The battery is rated 70 Wh and lasts up to 18 hours; price $1,499.00.",
    2: "Memory is 16 GB, soldered and “not upgradeable”.",
}


def _codes(text: str) -> list[str]:
    return [issue.code.value for issue in validate_citations(text, EVIDENCE).issues]


def test_valid_answer_report() -> None:
    report = validate_citations(
        "Battery lasts up to 18 hours [E1]. Memory is 16 GB and soldered [E2]. Nice.", EVIDENCE
    )
    assert report.valid
    assert report.sentences_total == 3
    assert report.sentences_requiring_citation == 2
    assert report.coverage == 1.0
    assert report.citations_total == report.citations_supported == 2
    assert report.cited_positions == [1, 2]


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("Battery lasts up to 20 hours [E1].", ["unsupported_number"]),
        ("It costs $1,499 in the US [E1].", []),
        ("It costs 1499.0 dollars today [E1].", []),
        ("It costs $1,599 in the US [E1].", ["unsupported_number"]),
        ('The memory is "not upgradeable" [E2].', []),
        ("The memory is “NOT  upgradeable” [E2].", []),
        ('The memory is "user replaceable" [E2].', ["unsupported_quote"]),
        ("Memory is 16 GB [E3].", ["unknown_citation"]),
        ("Memory is 16 GB [E2, E3].", ["unknown_citation"]),
        ("Memory is 16 GB [E2][E1].", []),
        ("This is a very good laptop overall.", ["uncited_sentence"]),
        ("Short line [E1].", []),
        ("Model X14 is rated 70 Wh [E1].", []),
    ],
)
def test_rules(text: str, codes: list[str]) -> None:
    assert _codes(text) == codes


def test_unknown_only_citation_counts_but_is_unsupported() -> None:
    report = validate_citations("The warranty lasts one full year [E9].", EVIDENCE)
    assert not report.valid
    assert report.sentences_cited == 1
    assert report.citations_total == 1
    assert report.citations_supported == 0
    assert report.cited_positions == []


def test_empty_text_and_sentence_split() -> None:
    report = validate_citations("", EVIDENCE)
    assert report.valid
    assert report.coverage == 1.0
    assert split_sentences("One is here. Two [E1]! 3 items? ok") == [
        "One is here.",
        "Two [E1]!",
        "3 items? ok",
    ]
    assert IssueCode("uncited_sentence") is IssueCode.UNCITED_SENTENCE


def test_labelled_benchmark() -> None:
    result = run()
    assert result["cases"] == 24
    assert result["case_accuracy"] == 1.0
    assert result["issue_precision"] == 1.0
    assert result["issue_recall"] == 1.0
