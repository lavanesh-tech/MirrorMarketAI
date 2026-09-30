"""RRF fusion, ranking metrics and search request validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.metrics import mean, recall_at_k, reciprocal_rank
from app.schemas.search import SearchRequest


def test_rrf_rewards_items_ranked_by_both_lists() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "d", "a"]], k=60)
    order = [item for item, _ in fused]
    assert order == ["b", "a", "d", "c"]
    scores = dict(fused)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["d"] == pytest.approx(1 / 62)


def test_rrf_single_list_preserves_order_and_ignores_duplicates() -> None:
    fused = reciprocal_rank_fusion([["x", "y", "x", "z"]])
    assert [item for item, _ in fused] == ["x", "y", "z"]
    assert dict(fused)["y"] == pytest.approx(1 / 62)


def test_rrf_handles_empty_inputs_and_rejects_bad_k() -> None:
    assert reciprocal_rank_fusion([[], []]) == []
    with pytest.raises(ValueError, match="k must be"):
        reciprocal_rank_fusion([["a"]], k=0)


def test_recall_at_k() -> None:
    assert recall_at_k(["a", "b", "c"], {"a", "c"}, 1) == 0.5
    assert recall_at_k(["a", "b", "c"], {"a", "c"}, 3) == 1.0
    assert recall_at_k([], {"a"}, 5) == 0.0
    with pytest.raises(ValueError, match="k must be"):
        recall_at_k(["a"], {"a"}, 0)
    with pytest.raises(ValueError, match="relevant"):
        recall_at_k(["a"], set(), 1)


def test_reciprocal_rank_and_mean() -> None:
    assert reciprocal_rank(["a", "b", "c"], {"c"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(["a"], {"z"}) == 0.0
    assert mean([1.0, 0.5]) == 0.75
    with pytest.raises(ValueError, match="empty"):
        mean([])


def test_search_request_normalizes_whitespace_and_rejects_blank() -> None:
    assert SearchRequest(query="  battery \n life ").query == "battery life"
    with pytest.raises(ValidationError):
        SearchRequest(query="   ")
    with pytest.raises(ValidationError):
        SearchRequest(query="x", limit=51)
    with pytest.raises(ValidationError):
        SearchRequest(query="x", mode="semantic")  # type: ignore[arg-type]
