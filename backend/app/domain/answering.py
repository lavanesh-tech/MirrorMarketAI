"""Grounded answering over an evidence pack.

The offline engine is extractive: it picks the evidence sentences that best cover
the question's terms (IDF-weighted) and returns them verbatim with their [E#]
marker, so every number and quote is supported by construction. It abstains when
no sentence covers enough of the question.

`enforce_citations` is the guard used for model-written answers: it keeps only
sentences that pass the citation validator (known markers, supported numbers and
quotes). If nothing survives, the answer becomes an abstention.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.citations import split_sentences, validate_citations

MIN_COVERAGE = 0.5
MAX_SENTENCES = 3
MIN_STEM = 3
_TOKEN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "much",
        "many",
        "my",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "there",
        "their",
        "they",
        "them",
        "should",
        "any",
        "about",
        "get",
        "got",
        "long",
        "later",
        "good",
        "bad",
        "included",
        "include",
        "come",
        "comes",
        "it's",
        "really",
        "very",
    ]
)


def _stem(token: str) -> str:
    """Tiny suffix stripper; a final "e" is dropped so upgrade/upgraded/upgradeable meet."""
    for suffix in ("ness", "able", "ing", "ed", "es", "ly", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    return token[:-1] if len(token) > MIN_STEM and token.endswith("e") else token


def terms(text: str) -> set[str]:
    return {_stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS}


@dataclass(frozen=True, slots=True)
class Answer:
    text: str
    abstained: bool
    cited: list[int]
    coverage: float  # best sentence's IDF-weighted share of question terms (extractive)


def extractive_answer(question: str, evidence: Mapping[int, str]) -> Answer:
    query = terms(question)
    sentences = [
        (position, sentence)
        for position, text in sorted(evidence.items())
        for sentence in split_sentences(text)
    ]
    if not query or not sentences:
        return Answer("", abstained=True, cited=[], coverage=0.0)
    sentence_terms = [terms(s) for _, s in sentences]
    n = len(sentences)
    idf = {t: math.log(1 + n / (1 + sum(t in st for st in sentence_terms))) for t in query}
    total = sum(idf.values())

    scored = []
    for index, ((position, sentence), st) in enumerate(zip(sentences, sentence_terms, strict=True)):
        coverage = sum(idf[t] for t in query & st) / total
        scored.append((coverage, -index, position, sentence))
    scored.sort(reverse=True)
    best = scored[0][0]
    if best < MIN_COVERAGE:
        return Answer("", abstained=True, cited=[], coverage=round(best, 4))
    chosen = [s for s in scored if s[0] >= max(MIN_COVERAGE, best * 0.8)][:MAX_SENTENCES]
    chosen.sort(key=lambda s: -s[1])  # keep document order
    text = " ".join(
        f"{sentence.rstrip('.!?')} [E{position}]." for _, _, position, sentence in chosen
    )
    cited = sorted({position for _, _, position, _ in chosen})
    return Answer(text, abstained=False, cited=cited, coverage=round(best, 4))


def enforce_citations(answer: str, evidence: Mapping[int, str]) -> tuple[str, list[str]]:
    """Drop sentences the validator rejects; returns (kept text, dropped sentences)."""
    kept: list[str] = []
    dropped: list[str] = []
    for sentence in split_sentences(answer):
        report = validate_citations(sentence, evidence)
        (kept if report.valid and report.cited_positions else dropped).append(sentence)
    return " ".join(kept), dropped
