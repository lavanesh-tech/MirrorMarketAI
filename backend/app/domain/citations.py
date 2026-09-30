"""Citation validation for generated text that cites evidence with [E1]-style markers.

Rules (deterministic, no LLM):
- every marker must point at an item in the evidence pack          -> unknown_citation
- every substantive sentence (>= MIN_WORDS words) must cite         -> uncited_sentence
- every number in a cited sentence must occur in its cited evidence -> unsupported_number
- every quoted phrase must occur verbatim in its cited evidence     -> unsupported_quote

The number and quote checks catch the most common RAG failure: a correct-looking
citation attached to a fabricated figure.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel

MIN_WORDS = 4

_MARKER_GROUP = re.compile(r"\[(E\d+(?:\s*,\s*E\d+)*)\]")
_MARKER_ID = re.compile(r"E(\d+)")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\w])")
_QUOTE = re.compile(r"[\"“]([^\"”]{3,300})[\"”]")
_QUOTES = str.maketrans({chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"'})
_WORD = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*")


class IssueCode(StrEnum):
    UNKNOWN_CITATION = "unknown_citation"
    UNCITED_SENTENCE = "uncited_sentence"
    UNSUPPORTED_NUMBER = "unsupported_number"
    UNSUPPORTED_QUOTE = "unsupported_quote"


class CitationIssue(BaseModel):
    code: IssueCode
    sentence: int  # 0-based sentence index
    detail: str


class CitationReport(BaseModel):
    valid: bool
    sentences_total: int
    sentences_requiring_citation: int
    sentences_cited: int
    coverage: float  # cited / requiring citation (1.0 when nothing requires one)
    citations_total: int
    citations_supported: int
    cited_positions: list[int]
    issues: list[CitationIssue]


def _normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = folded.translate(_QUOTES)
    return " ".join(folded.split())


def _numbers(text: str) -> set[str]:
    """Canonical numbers: '1,500' -> '1500', '16.0' -> '16', '1.40' -> '1.4'."""
    found: set[str] = set()
    for whole, fraction in _NUMBER.findall(text):
        integer = whole.replace(",", "").lstrip("0") or "0"
        decimals = (fraction or "").rstrip("0")
        found.add(f"{integer}.{decimals}" if decimals else integer)
    return found


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(" ".join(text.split())) if s.strip()]


def validate_citations(text: str, evidence: Mapping[int, str]) -> CitationReport:
    """Validate `text` against evidence texts keyed by 1-based position (E1 -> 1)."""
    normalized_evidence = {pos: _normalize(body) for pos, body in evidence.items()}
    evidence_numbers = {pos: _numbers(body) for pos, body in evidence.items()}

    issues: list[CitationIssue] = []
    sentences = split_sentences(text)
    requiring = cited = citations_total = citations_supported = 0
    cited_positions: set[int] = set()

    for index, sentence in enumerate(sentences):
        markers = [
            int(n) for group in _MARKER_GROUP.findall(sentence) for n in _MARKER_ID.findall(group)
        ]
        claim = _MARKER_GROUP.sub(" ", sentence)
        if len(_WORD.findall(claim)) >= MIN_WORDS:
            requiring += 1
            if markers:
                cited += 1
            else:
                issues.append(
                    CitationIssue(
                        code=IssueCode.UNCITED_SENTENCE, sentence=index, detail=sentence[:200]
                    )
                )

        known = [m for m in dict.fromkeys(markers) if m in evidence]
        for marker in dict.fromkeys(markers):
            if marker not in evidence:
                issues.append(
                    CitationIssue(
                        code=IssueCode.UNKNOWN_CITATION, sentence=index, detail=f"E{marker}"
                    )
                )
        citations_total += len(set(markers))
        cited_positions.update(known)
        if not known:
            continue

        sentence_ok = True
        supported_numbers = set().union(*(evidence_numbers[m] for m in known))
        for number in sorted(_numbers(claim) - supported_numbers):
            sentence_ok = False
            issues.append(
                CitationIssue(code=IssueCode.UNSUPPORTED_NUMBER, sentence=index, detail=number)
            )
        for quote in _QUOTE.findall(claim):
            needle = _normalize(quote)
            if not any(needle in normalized_evidence[m] for m in known):
                sentence_ok = False
                issues.append(
                    CitationIssue(
                        code=IssueCode.UNSUPPORTED_QUOTE, sentence=index, detail=quote[:200]
                    )
                )
        if sentence_ok:
            citations_supported += len(known)

    return CitationReport(
        valid=not issues,
        sentences_total=len(sentences),
        sentences_requiring_citation=requiring,
        sentences_cited=cited,
        coverage=round(cited / requiring, 4) if requiring else 1.0,
        citations_total=citations_total,
        citations_supported=citations_supported,
        cited_positions=sorted(cited_positions),
        issues=issues,
    )
