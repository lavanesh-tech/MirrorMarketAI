"""Ranking-quality metrics for retrieval evaluation (used by tests and Phase 27)."""

from __future__ import annotations

from collections.abc import Collection, Sequence


def recall_at_k[T](ranked: Sequence[T], relevant: Collection[T], k: int) -> float:
    """Share of relevant items that appear in the top ``k`` results."""
    if k < 1:
        raise ValueError("k must be >= 1")
    if not relevant:
        raise ValueError("relevant must not be empty")
    top = set(ranked[:k])
    return sum(1 for item in relevant if item in top) / len(relevant)


def reciprocal_rank[T](ranked: Sequence[T], relevant: Collection[T]) -> float:
    """1 / rank of the first relevant result, or 0 when none is returned."""
    for position, item in enumerate(ranked, start=1):
        if item in relevant:
            return 1.0 / position
    return 0.0


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("values must not be empty")
    return sum(values) / len(values)
