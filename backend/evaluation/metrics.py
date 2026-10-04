"""Scoring functions for the evaluation. Pure, so each one is unit-tested."""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence


def rank_of[T](ranked: Sequence[T], relevant: T) -> int | None:
    """1-based position of the relevant item, or None when it was not returned."""
    for position, item in enumerate(ranked, start=1):
        if item == relevant:
            return position
    return None


def hit_at(rank: int | None, k: int) -> float:
    return 1.0 if rank is not None and rank <= k else 0.0


def reciprocal(rank: int | None) -> float:
    return 0.0 if rank is None else 1.0 / rank


def ndcg_at(rank: int | None, k: int) -> float:
    """nDCG@k with one relevant item: the ideal DCG is 1, so this is the discounted gain."""
    return 0.0 if rank is None or rank > k else 1.0 / math.log2(rank + 1)


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def ratio(part: int, whole: int) -> float:
    """part / whole rounded for reports; 0 when there is nothing to divide by."""
    return round(part / whole, 4) if whole else 0.0


def precision_recall_f1(true_positive: int, predicted: int, expected: int) -> dict[str, float]:
    precision = true_positive / predicted if predicted else 0.0
    recall = true_positive / expected if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def same_number(a: object, b: float, tolerance: float = 1e-6) -> bool:
    """Compare an API value (number, numeric string or None) with a gold number."""
    try:
        return a is not None and abs(float(str(a)) - b) <= tolerance
    except ValueError:
        return False


def contains_any(text: str, fragments: Collection[str]) -> bool:
    lowered = text.casefold()
    return any(fragment.casefold() in lowered for fragment in fragments)
