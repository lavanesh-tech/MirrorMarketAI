from __future__ import annotations

import pytest

from app.repositories.base import MAX_PAGE_SIZE, PageRequest

pytestmark = pytest.mark.unit


def test_defaults() -> None:
    page = PageRequest()
    assert (page.limit, page.offset) == (20, 0)


@pytest.mark.parametrize("limit", [1, MAX_PAGE_SIZE])
def test_accepts_limits_within_bounds(limit: int) -> None:
    assert PageRequest(limit=limit).limit == limit


@pytest.mark.parametrize("limit", [0, -1, MAX_PAGE_SIZE + 1])
def test_rejects_out_of_range_limit(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        PageRequest(limit=limit)


def test_rejects_negative_offset() -> None:
    with pytest.raises(ValueError, match="offset"):
        PageRequest(offset=-1)
