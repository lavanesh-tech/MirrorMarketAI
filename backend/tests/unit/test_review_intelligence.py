"""Review Intelligence Agent building blocks (no database)."""

from __future__ import annotations

import uuid

import pytest

from app.agents.base import Polarity
from app.agents.review_intelligence import (
    Mention,
    aggregate,
    classify_sentence,
    parse_llm_mentions,
    rule_mentions,
)
from app.domain.citations import validate_citations
from app.models.catalog import Product
from app.providers.llm import JsonCompletion
from benchmarks.review_sentiment import run

pytestmark = pytest.mark.unit

P, N = Polarity.POSITIVE, Polarity.NEGATIVE


def _product() -> Product:
    return Product(id=uuid.uuid4(), brand="Acme", name="L14", category="laptop")


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("Battery life is not great.", [("battery", N)]),
        ("The fans are not loud at all.", [("thermals", P)]),
        ("Keyboard is mushy but the speakers are excellent.", [("keyboard", N), ("sound", P)]),
        ("It arrived on Tuesday.", []),
        ("The screen exists.", []),
    ],
)
def test_classify_sentence(sentence: str, expected: list[tuple[str, Polarity]]) -> None:
    assert classify_sentence(sentence) == expected


def test_aggregate_counts_sentiment_and_cited_summary() -> None:
    evidence = {
        1: "The battery lasts all day. The keyboard is mushy.",
        2: "Battery life is excellent. The keyboard feels cramped.",
        3: "Battery is weak on video. The display is bright.",
        4: "The display is dim outdoors.",
    }
    out = aggregate(_product(), rule_mentions(evidence), review_chunks=4)
    by = {a.aspect: a for a in out.aspects}
    assert (by["battery"].positive, by["battery"].negative) == (2, 1)
    assert by["battery"].sentiment is Polarity.MIXED
    assert by["keyboard"].sentiment is Polarity.NEGATIVE
    assert by["display"].sentiment is Polarity.MIXED
    assert out.complaints == ["keyboard"]
    assert out.praises == []
    assert out.review_chunks == 4
    assert out.opinion_sentences == 7
    assert out.overall == round((3 - 4) / 7, 4)
    assert out.summary.startswith("Reviewers are divided on the battery life [E1, E2, E3].")
    assert validate_citations(out.summary, evidence).valid


def test_aggregate_praise_and_empty() -> None:
    mentions = [Mention("sound", P, 1, "Great sound."), Mention("sound", P, 2, "Superb audio.")]
    out = aggregate(_product(), mentions, review_chunks=2)
    assert out.praises == ["sound"]
    assert out.summary == "Reviewers mostly praise the sound quality [E1, E2]."
    empty = aggregate(_product(), [], review_chunks=0)
    assert (empty.summary, empty.overall, empty.aspects) == ("", 0.0, [])


def test_parse_llm_mentions_guards() -> None:
    evidence = {1: "The battery is weak. Love the screen!", 2: "Speakers are tinny."}
    completion = JsonCompletion(
        {
            "opinions": [
                {
                    "marker": "E1",
                    "aspect": "battery",
                    "polarity": "NEGATIVE",
                    "quote": "The battery is weak.",
                },
                {
                    "marker": "E1",
                    "aspect": "battery",
                    "polarity": "NEGATIVE",
                    "quote": "the BATTERY  is weak.",
                },
                {
                    "marker": "E1",
                    "aspect": "display",
                    "polarity": "POSITIVE",
                    "quote": "Love the screen!",
                },
                {
                    "marker": "E2",
                    "aspect": "sound",
                    "polarity": "POSITIVE",
                    "quote": "Speakers are amazing.",
                },
                {
                    "marker": "E7",
                    "aspect": "sound",
                    "polarity": "NEGATIVE",
                    "quote": "Speakers are tinny.",
                },
                {
                    "marker": "E2",
                    "aspect": "teleport",
                    "polarity": "NEGATIVE",
                    "quote": "Speakers are tinny.",
                },
                {
                    "marker": "X2",
                    "aspect": "sound",
                    "polarity": "NEGATIVE",
                    "quote": "Speakers are tinny.",
                },
                {"marker": "E2", "aspect": "sound", "polarity": "NEGATIVE", "quote": "ok"},
                "junk",
            ]
        },
        tokens_used=3,
    )
    mentions = parse_llm_mentions(completion, evidence)
    assert [(m.aspect, m.polarity, m.position) for m in mentions] == [
        ("battery", N, 1),
        ("display", P, 1),
    ]


def test_review_benchmark_floor() -> None:
    result = run()
    assert result["cases"] == 24
    assert result["pair_precision"] == 1.0
    assert result["pair_f1"] == 0.9333
