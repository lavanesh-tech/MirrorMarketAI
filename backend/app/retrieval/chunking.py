"""Deterministic, structure-aware text chunking.

Strategy: split into sentences (never across paragraph breaks), hard-cut any
single run longer than the target, then pack pieces greedily up to
`target_chars`. Consecutive
chunks share up to `overlap_chars` of trailing context so facts that straddle
a boundary remain retrievable.

Every chunk records [char_start, char_end) offsets into the source document so
citations (Phase 9) can point to the exact span. Character counts are used
instead of model tokens (~4 chars/token for English) to stay dependency-free
and deterministic; `estimate_tokens` is used only for reporting/budgeting.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    text: str
    char_start: int
    char_end: int

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _spans(text: str, pattern: re.Pattern[str], start: int, end: int) -> list[tuple[int, int]]:
    """Split text[start:end] on `pattern`, returning absolute (start, end) spans."""
    spans: list[tuple[int, int]] = []
    cursor = start
    for match in pattern.finditer(text, start, end):
        if match.start() > cursor:
            spans.append((cursor, match.start()))
        cursor = match.end()
    if cursor < end:
        spans.append((cursor, end))
    return spans


def _atomic_spans(text: str, target: int) -> list[tuple[int, int]]:
    """Break the document into pieces no longer than `target` chars."""
    pieces: list[tuple[int, int]] = []
    for p_start, p_end in _spans(text, _PARAGRAPH, 0, len(text)):
        # Sentence-level pieces: packing keeps paragraphs together when they
        # fit, and overlap can be taken at sentence granularity.
        for sentence_start, s_end in _spans(text, _SENTENCE_END, p_start, p_end):
            s_start = sentence_start
            while s_end - s_start > target:  # e.g. a huge table row with no punctuation
                cut = text.rfind(" ", s_start, s_start + target)
                cut = cut if cut > s_start else s_start + target
                pieces.append((s_start, cut))
                s_start = cut
                while s_start < s_end and text[s_start].isspace():
                    s_start += 1
            if s_end > s_start:
                pieces.append((s_start, s_end))
    return pieces


def chunk_text(text: str, *, target_chars: int, overlap_chars: int) -> list[Chunk]:
    if overlap_chars >= target_chars // 2:
        raise ValueError("overlap must be less than half of the target size")
    if not text.strip():
        return []

    pieces = _atomic_spans(text, target_chars)
    chunks: list[Chunk] = []
    i = 0
    while i < len(pieces):
        start = pieces[i][0]
        end = pieces[i][1]
        j = i + 1
        while j < len(pieces) and pieces[j][1] - start <= target_chars:
            end = pieces[j][1]
            j += 1
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        chunks.append(Chunk(len(chunks), text[start:end], start, end))
        if j >= len(pieces):
            break
        # Start the next chunk early enough to repeat ~overlap_chars of context,
        # but always make forward progress.
        next_i = j
        while next_i - 1 > i and end - pieces[next_i - 1][0] <= overlap_chars:
            next_i -= 1
        i = next_i
    return chunks
