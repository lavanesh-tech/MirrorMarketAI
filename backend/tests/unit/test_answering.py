"""Grounded answering (pure)."""

from __future__ import annotations

import pytest

from app.domain.answering import enforce_citations, extractive_answer, terms
from app.domain.citations import validate_citations
from benchmarks.qa_extractive import run

pytestmark = pytest.mark.unit

EVIDENCE = {
    1: "The battery lasts up to 18 hours. It charges over USB-C.",
    2: "Memory is 16 GB and not upgradeable.",
}


def test_terms_drop_stopwords_and_stem() -> None:
    assert terms("How much memory does it have?") == {"memory"}
    assert terms("Is it upgradeable? Brightness!") == {"upgrad", "bright"}


def test_extractive_answer_cites_verbatim_sentences() -> None:
    answer = extractive_answer("How long does the battery last?", EVIDENCE)
    assert answer.text == "The battery lasts up to 18 hours [E1]."
    assert (answer.abstained, answer.cited) == (False, [1])
    assert validate_citations(answer.text, EVIDENCE).valid
    upgrade = extractive_answer("Can the memory be upgraded?", EVIDENCE)
    assert upgrade.cited == [2]


def test_abstains() -> None:
    assert extractive_answer("What colours exist?", EVIDENCE).abstained
    assert extractive_answer("the and of", EVIDENCE).abstained
    assert extractive_answer("battery", {}).abstained


def test_enforce_citations_drops_unsupported_sentences() -> None:
    kept, dropped = enforce_citations(
        "The battery lasts up to 18 hours [E1]. It lasts 30 hours on standby [E1]. "
        "Memory is 16 GB [E9]. Great laptop overall for most people.",
        EVIDENCE,
    )
    assert kept == "The battery lasts up to 18 hours [E1]."
    assert len(dropped) == 3


def test_qa_benchmark() -> None:
    result = run()
    assert result["accuracy"] == 1.0
    assert result["held_out_accuracy"] == 0.8182  # 9/11; misses documented in the result
