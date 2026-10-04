"""The evaluation's own scoring and data must be right before its numbers mean anything."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from evaluation import dataset as ds
from evaluation.metrics import (
    contains_any,
    hit_at,
    mean,
    ndcg_at,
    precision_recall_f1,
    rank_of,
    ratio,
    reciprocal,
    same_number,
)
from evaluation.report import render

pytestmark = pytest.mark.unit

RESULTS = Path(__file__).resolve().parents[2] / "evaluation" / "results"
BASELINES = RESULTS.parent / "baseline"


def test_ranking_metrics() -> None:
    assert rank_of(["a", "b", "c"], "c") == 3
    assert rank_of(["a"], "z") is None
    assert [hit_at(2, 1), hit_at(2, 3), hit_at(None, 10)] == [0.0, 1.0, 0.0]
    assert [reciprocal(4), reciprocal(None)] == [0.25, 0.0]
    assert ndcg_at(1, 5) == 1.0
    assert ndcg_at(3, 5) == pytest.approx(1 / math.log2(4))
    assert ndcg_at(6, 5) == 0.0
    assert ndcg_at(None, 5) == 0.0
    assert mean([1.0, 0.0]) == 0.5
    assert mean([]) == 0.0


def test_precision_recall_and_ratios_handle_empty_cases() -> None:
    assert precision_recall_f1(3, 4, 6) == {"precision": 0.75, "recall": 0.5, "f1": 0.6}
    assert precision_recall_f1(0, 0, 5) == {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    assert ratio(1, 3) == 0.3333
    assert ratio(0, 0) == 0.0


def test_value_matching() -> None:
    assert same_number("16.000000", 16)
    assert same_number(0.99, 0.99)
    assert not same_number(None, 16)
    assert not same_number("sixteen", 16)
    assert not same_number("512", 16)
    assert contains_any("Battery life is 12 HOURS [E1].", ("12 hours",))
    assert not contains_any("It lasts a day.", ("12 hours",))


def test_every_label_points_at_something_that_exists() -> None:
    main = {p.key: p for p in ds.PRODUCTS}
    assert len(main) == len(ds.PRODUCTS)
    for query in ds.QUERIES:
        assert query.doc in main[query.product].docs
    assert len({q.text for q in ds.QUERIES}) == len(ds.QUERIES)
    for corpus in (ds.MAIN, ds.HELD_OUT):
        products = {p.key: p for p in corpus.products}
        for product in corpus.products:
            assert set(product.facts) == {*corpus.fact_keys, "price"}
        for question in corpus.questions:
            assert question.product in products
            # The question names its product, as a buyer comparing several would.
            product = products[question.product]
            assert product.name in question.text or product.brand in question.text
        for poison in corpus.poisons:
            assert poison.fact_key in products[poison.product].facts
            assert products[poison.product].facts[poison.fact_key] != poison.false_value
        for scenario in corpus.scenarios:
            assert {c["key"] for c in scenario.criteria} <= set(corpus.fact_keys)
    # Held-out really is separate.
    assert not {p.key for p in ds.HELD_OUT_PRODUCTS} & set(main)
    assert not {q.text for q in ds.HELD_OUT_QUESTIONS} & {q.text for q in ds.QUESTIONS}


def test_accepted_answers_really_are_in_the_documents() -> None:
    """A gold answer the corpus does not contain would make every system look wrong."""
    for corpus in (ds.MAIN, ds.HELD_OUT):
        text = {p.key: " ".join(p.docs.values()).casefold() for p in corpus.products}
        for question in corpus.questions:
            if question.accept is not None:
                assert any(fragment in text[question.product] for fragment in question.accept), (
                    question.text
                )


def test_scenarios_qualify_the_products_the_facts_say_they_should() -> None:
    qualifying = [sorted(s.qualifying(ds.PRODUCTS)) for s in ds.SCENARIOS]
    assert qualifying == [
        ["aster", "borealis", "ember", "fjord"],
        ["aster", "cinder", "fjord"],
        ["borealis", "dune"],
        ["borealis"],
    ]
    # Every scenario rules something out and lets something through.
    assert all(0 < len(q) < len(ds.PRODUCTS) for q in qualifying)
    assert ds.HELD_OUT_SCENARIO.qualifying(ds.HELD_OUT_PRODUCTS) == {"sono", "vela", "orrin"}


def test_the_report_is_rendered_from_the_committed_results() -> None:
    results = {p.stem: json.loads(p.read_text()) for p in sorted(RESULTS.glob("*.json"))}
    baselines = {p.stem: json.loads(p.read_text()) for p in sorted(BASELINES.glob("*.json"))}
    assert "rules-hashing" in results
    report = render(results, baselines)
    committed = (RESULTS.parents[2] / "docs" / "EVALUATION.md").read_text()
    assert report == committed, "run `make eval-report` and commit docs/EVALUATION.md"
    assert "synthetic" in report
    assert "Held-out set" in report
