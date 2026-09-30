"""Reciprocal Rank Fusion (Cormack et al., 2009).

score(d) = sum over rankings of 1 / (k + rank(d)), rank starting at 1.

RRF only uses ranks, so it can merge BM25-style text scores and cosine
distances without calibrating their very different scales.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence

DEFAULT_RRF_K = 60


def reciprocal_rank_fusion[K: Hashable](
    rankings: Sequence[Sequence[K]], *, k: int = DEFAULT_RRF_K
) -> list[tuple[K, float]]:
    """Fuse ranked lists; ties keep first-seen order, so output is deterministic."""
    if k < 1:
        raise ValueError("k must be >= 1")
    scores: dict[K, float] = {}
    for ranking in rankings:
        seen: set[K] = set()
        for rank, item in enumerate(ranking, start=1):
            if item in seen:
                continue
            seen.add(item)
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: -pair[1])
