from __future__ import annotations

import itertools

import pytest

from app.retrieval.chunking import chunk_text, estimate_tokens

pytestmark = pytest.mark.unit


def _doc(paragraphs: int, sentences: int = 5) -> str:
    return "\n\n".join(
        " ".join(f"Paragraph {p} sentence {s} has some words." for s in range(sentences))
        for p in range(paragraphs)
    )


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_text("   \n\n ", target_chars=500, overlap_chars=50) == []


def test_short_text_is_one_chunk() -> None:
    chunks = chunk_text("Battery: 70 Wh.", target_chars=500, overlap_chars=50)
    assert [(c.index, c.text, c.char_start, c.char_end) for c in chunks] == [
        (0, "Battery: 70 Wh.", 0, 15)
    ]


@pytest.mark.parametrize(("target", "overlap"), [(300, 0), (300, 100), (1000, 200), (200, 50)])
def test_chunks_respect_size_offsets_and_cover_everything(target: int, overlap: int) -> None:
    text = _doc(12)
    chunks = chunk_text(text, target_chars=target, overlap_chars=overlap)

    assert [c.index for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert len(chunk.text) <= target
        assert text[chunk.char_start : chunk.char_end] == chunk.text  # offsets are exact
    # Every sentence of the document appears in at least one chunk.
    for sentence in text.replace("\n\n", " ").split(". "):
        assert any(sentence.strip(". ") in c.text for c in chunks)
    # Offsets never go backwards.
    starts = [c.char_start for c in chunks]
    assert starts == sorted(starts)


def test_overlap_repeats_context_between_neighbours() -> None:
    chunks = chunk_text(_doc(10), target_chars=300, overlap_chars=120)
    overlapping = [a for a, b in itertools.pairwise(chunks) if b.char_start < a.char_end]
    assert overlapping, "expected at least one overlapping pair"


def test_no_overlap_when_disabled() -> None:
    chunks = chunk_text(_doc(10), target_chars=300, overlap_chars=0)
    for a, b in itertools.pairwise(chunks):
        assert b.char_start >= a.char_end


def test_giant_unpunctuated_run_is_hard_split() -> None:
    text = "spec " * 400  # 2000 chars, no sentence boundaries
    chunks = chunk_text(text.strip(), target_chars=250, overlap_chars=0)
    assert len(chunks) >= 8
    assert all(len(c.text) <= 250 for c in chunks)


def test_word_without_spaces_longer_than_target_is_cut() -> None:
    chunks = chunk_text("x" * 1000, target_chars=300, overlap_chars=0)
    assert [len(c.text) for c in chunks] == [300, 300, 300, 100]


def test_chunking_is_deterministic() -> None:
    text = _doc(20)
    first = chunk_text(text, target_chars=400, overlap_chars=80)
    second = chunk_text(text, target_chars=400, overlap_chars=80)
    assert first == second
    assert first[0].content_hash == second[0].content_hash


def test_overlap_must_be_small() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk_text("x", target_chars=100, overlap_chars=60)


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 400) == 100
