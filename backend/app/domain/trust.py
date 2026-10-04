"""How much weight a source gets when sources disagree about a product.

A buyer trusts a specification sheet over a review, and a review over an anonymous
note, for the same reason this module exists: anyone can add a document that says
"this laptop has 64 GB of RAM". Preferring documents of record means such a claim
only wins when nothing better says otherwise. It lowers the risk of poisoned or
simply mistaken sources; it does not remove it (see docs/EVALUATION.md).
"""

from __future__ import annotations

OFFICIAL = "OFFICIAL"
# Documents a manufacturer or seller stands behind.
_RECORD = frozenset(
    {"SPECIFICATION_SHEET", "MANUFACTURER_PAGE", "MANUAL", "WARRANTY", "RETURN_POLICY"}
)
_OPINION = frozenset({"REVIEW"})
# Multipliers for answer selection, by tier (record, opinion, anything else).
_WEIGHTS = (1.0, 0.9, 0.8)


def tier(source_type: str) -> int:
    """0 = document of record, 1 = review, 2 = notes and everything else."""
    if source_type in _RECORD:
        return 0
    return 1 if source_type in _OPINION else 2


def fact_rank(source_type: str, authority: str) -> tuple[int, int]:
    """Sort key for product facts: official first, then by kind of document."""
    return (0 if authority == OFFICIAL else 1, tier(source_type))


def answer_weight(source_type: str) -> float:
    """A mild preference, so a strong match in a review still beats a weak one elsewhere."""
    return _WEIGHTS[tier(source_type)]
