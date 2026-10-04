"""Deterministic, offline requirement extraction (English).

Turns a free-text brief such as

    "Need a laptop under $1,500 for programming and travel. Must have at least
     16 GB RAM, ideally under 1.4 kg. No touchscreen. Avoid Dell."

into a `RequirementSpec`. It is intentionally conservative: it only emits what
it recognises with high confidence, and reports clauses it could not use as
`unparsed` so the UI can ask the user to confirm. The OpenAI extractor handles
richer language; this one keeps tests, CI and the demo free of API calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.requirements import Budget, Criterion, Operator, Priority, RequirementSpec

# ------------------------------------------------------------------ vocabulary
_CATEGORY_WORDS: dict[str, tuple[str, ...]] = {
    "laptop": ("laptop", "notebook", "ultrabook", "macbook", "chromebook"),
    "desktop": ("desktop", "tower pc", "workstation"),
    "monitor": ("monitor", "display for my desk", "external display"),
    "phone": ("phone", "smartphone", "iphone"),
    "tablet": ("tablet", "ipad"),
    "headphones": ("headphones", "headphone", "earbuds", "headset"),
    "camera": ("camera", "mirrorless", "dslr"),
    "appliance": ("fridge", "refrigerator", "washer", "dishwasher", "vacuum", "microwave"),
}

_USE_CASES: dict[str, tuple[str, ...]] = {
    "programming": ("programming", "coding", "software development", "developer"),
    "gaming": ("gaming", "games"),
    "video editing": ("video editing", "editing video", "premiere", "final cut"),
    "photo editing": ("photo editing", "photography", "lightroom"),
    "travel": ("travel", "traveling", "travelling", "commute", "commuting", "portable"),
    "office work": ("office", "spreadsheets", "email", "documents"),
    "students": ("school", "student", "college", "university"),
    "music production": ("music production", "audio production", "recording"),
    "workouts": ("running", "gym", "workout", "workouts"),
    "calls": ("calls", "meetings", "zoom"),
}

# (regex matching the feature, criterion key)
_FEATURES: tuple[tuple[str, str], ...] = (
    (r"thunderbolt", "has_thunderbolt"),
    (r"touch ?screen", "has_touchscreen"),
    (r"backlit keyboard", "has_backlit_keyboard"),
    (r"(?:active )?noise[- ]cancel(?:l)?(?:ing|ation)|\banc\b", "has_noise_cancellation"),
    (r"oled", "has_oled_display"),
    (r"usb[- ]?c", "has_usb_c"),
    (r"hdmi", "has_hdmi"),
    (r"wireless charging", "has_wireless_charging"),
    (r"water[- ]?(?:proof|resistant|resistance)", "is_water_resistant"),
    (r"headphone jack|3\.5 ?mm", "has_headphone_jack"),
    (r"fingerprint", "has_fingerprint_reader"),
    (r"sd card|card reader", "has_sd_card_reader"),
)

_MUST_WORDS = re.compile(
    r"\b(must|need|needs|required|require|requires|have to|has to|at least|minimum|"
    r"essential|mandatory|non-negotiable|only)\b",
    re.I,
)
_SHOULD_WORDS = re.compile(
    r"\b(prefer|preferably|ideally|nice to have|would like|bonus|if possible|"
    r"would be nice|rather)\b",
    re.I,
)
_NEGATION = re.compile(r"\b(no|not|without|don't want|do not want|avoid|never)\b", re.I)

_NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
_LOWER = r"under|below|less than|at most|max(?:imum)?|up to|no more than|lighter than|<=?"
_UPPER = r"at least|min(?:imum)?|more than|over|above|or more|\+|>=?"

# (unit regex, noun regex that must appear near the number or None, key, unit, factor, default op)
_QUANTITIES: tuple[tuple[str, str | None, str, str, Decimal, Operator], ...] = (
    (r"gb", r"ram|memory", "ram_gb", "GB", Decimal(1), Operator.GTE),
    (r"tb", r"storage|ssd|disk|drive", "storage_gb", "GB", Decimal(1024), Operator.GTE),
    (r"gb", r"storage|ssd|disk|drive", "storage_gb", "GB", Decimal(1), Operator.GTE),
    (r"hours?|hrs?|h", r"battery", "battery_life_hours", "h", Decimal(1), Operator.GTE),
    (r"kg|kilos?|kilograms?", None, "weight_kg", "kg", Decimal(1), Operator.LTE),
    (r"lbs?|pounds?", None, "weight_kg", "kg", Decimal("0.45359237"), Operator.LTE),
    (r"g|grams?", r"weigh|weight|light", "weight_kg", "kg", Decimal("0.001"), Operator.LTE),
    (
        r"inch(?:es)?|in\b|\"",
        r"screen|display|monitor",
        "screen_size_in",
        "in",
        Decimal(1),
        Operator.GTE,
    ),
    (r"hz", None, "refresh_rate_hz", "Hz", Decimal(1), Operator.GTE),
    (r"nits", None, "brightness_nits", "nits", Decimal(1), Operator.GTE),
    (r"wh", None, "battery_wh", "Wh", Decimal(1), Operator.GTE),
    (r"mah", None, "battery_mah", "mAh", Decimal(1), Operator.GTE),
)

_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP"}
_BUDGET_RANGE = re.compile(
    rf"([$€£])\s?{_NUMBER}\s*(k)?\s*(?:-|\u2013|to|and)\s*[$€£]?\s?{_NUMBER}\s*(k)?", re.I
)
_BUDGET_MAX = re.compile(
    rf"(?:{_LOWER}|budget(?: of| is)?|spend(?:ing)?|around|about|~)\s*([$€£])\s?{_NUMBER}\s*(k)?",
    re.I,
)
_BUDGET_BARE = re.compile(rf"([$€£])\s?{_NUMBER}\s*(k)?", re.I)
_EXCLUDED_BRAND = re.compile(
    r"\b(?i:avoid|avoiding|excluding|except|not from|no|nothing from|don't want|anything but)"
    r"\s+([A-Z][A-Za-z0-9&-]{1,30})"
)
_OWNED = re.compile(
    r"\b(?:i (?:have|own|use|already have)|works? with|compatible with|connects? to|pair with)"
    r"\s+(?:an?\s+|my\s+|the\s+)?([A-Za-z0-9][^,.;!?]{1,60})",
    re.I,
)
_OWNED_SPLIT = re.compile(r"\s+and\s+(?:an?\s+|my\s+|the\s+)?", re.I)
_CLAUSE_SPLIT = re.compile(r"[;,]\s+(?:and\s+|but\s+)?|\s+but\s+")


@dataclass(slots=True)
class RuleExtraction:
    spec: RequirementSpec
    unparsed: list[str] = field(default_factory=list)


def _plain(value: Decimal) -> Decimal:
    """16.000 -> 16 and 1200.0 -> 1200, without switching to exponent notation."""
    return Decimal(format(value.normalize(), "f"))


def _amount(raw: str, thousands: str | None) -> Decimal:
    value = Decimal(raw.replace(",", ""))
    return _plain(value * 1000 if thousands else value)


def _priority(clause: str, sentence: str) -> Priority:
    text = clause if (_MUST_WORDS.search(clause) or _SHOULD_WORDS.search(clause)) else sentence
    if _SHOULD_WORDS.search(text):
        return Priority.SHOULD
    if _MUST_WORDS.search(text):
        return Priority.MUST
    return Priority.SHOULD


def _extract_budget(text: str) -> Budget | None:
    if match := _BUDGET_RANGE.search(text):
        symbol, low, low_k, high, high_k = match.groups()
        low_amount, high_amount = _amount(low, low_k), _amount(high, high_k or low_k)
        if low_amount <= high_amount:
            return Budget(min_amount=low_amount, max_amount=high_amount, currency=_CURRENCY[symbol])
    if match := _BUDGET_MAX.search(text):
        symbol, amount, thousands = match.groups()
        return Budget(max_amount=_amount(amount, thousands), currency=_CURRENCY[symbol])
    if match := _BUDGET_BARE.search(text):
        symbol, amount, thousands = match.groups()
        return Budget(max_amount=_amount(amount, thousands), currency=_CURRENCY[symbol])
    return None


def _quantity_criteria(clause: str, sentence: str) -> list[Criterion]:
    found: dict[str, Criterion] = {}
    lowered = clause.lower()
    for unit_re, noun_re, key, unit, factor, default_op in _QUANTITIES:
        if key in found:
            continue
        pattern = re.compile(
            rf"(?:({_LOWER}|{_UPPER})\s*)?{_NUMBER}\s*-?\s*(?:{unit_re})(?![a-z])"
            r"\s*(or more|\+|or less)?",
            re.I,
        )
        match = _closest_to_noun(list(pattern.finditer(lowered)), noun_re, lowered)
        if match is not None:
            qualifier = (match.group(1) or match.group(3) or "").strip()
            if re.fullmatch(_UPPER, qualifier) or qualifier in ("or more", "+"):
                operator = Operator.GTE
            elif re.fullmatch(_LOWER, qualifier) or qualifier == "or less":
                operator = Operator.LTE
            else:
                operator = default_op
            value = (Decimal(match.group(2).replace(",", "")) * factor).quantize(Decimal("0.001"))
            found[key] = Criterion(
                key=key,
                operator=operator,
                value_number=_plain(value),
                unit=unit,
                priority=_priority(clause, sentence),
            )
    return list(found.values())


_NOUN_WINDOW = 30
_JOINS = re.compile(r",|;|\b(?:and|with|plus|but)\b")


def _closest_to_noun(
    matches: list[re.Match[str]], noun_re: str | None, text: str
) -> re.Match[str] | None:
    """The number that belongs to the noun, within 30 characters of it.

    "16 GB of memory with a 256 GB SSD" has two GB numbers near both nouns. A number
    joined to the noun directly ("16 GB of memory", "256 GB SSD", "storage is 512 GB")
    beats one separated from it by "and", "with" or a comma; among equals the nearest
    wins, then the first.
    """
    if noun_re is None:
        return matches[0] if matches else None
    nouns = [(n.start(), n.end()) for n in re.finditer(noun_re, text)]
    best: tuple[tuple[bool, int], re.Match[str]] | None = None
    for match in matches:
        for start, end in nouns:
            gap = max(start - match.end(), match.start() - end, 0)
            if gap > _NOUN_WINDOW:
                continue
            between = (
                text[match.end() : start] if start >= match.end() else text[end : match.start()]
            )
            rank = (bool(_JOINS.search(between)), gap)
            if best is None or rank < best[0]:
                best = (rank, match)
    return best[1] if best else None


def _feature_criteria(clause: str, sentence: str) -> list[Criterion]:
    criteria: list[Criterion] = []
    for feature_re, key in _FEATURES:
        match = re.search(feature_re, clause, re.I)
        if not match:
            continue
        before = clause[: match.start()]
        negated = bool(_NEGATION.search(before[-25:]))
        criteria.append(
            Criterion(
                key=key,
                operator=Operator.EQ,
                value_text="no" if negated else "yes",
                priority=Priority.MUST if negated else _priority(clause, sentence),
            )
        )
    return criteria


def _feature_word(word: str) -> bool:
    return any(re.fullmatch(feature_re, word, re.I) for feature_re, _ in _FEATURES)


def extract_requirements(text: str) -> RuleExtraction:
    """Best-effort structured extraction. Never raises on user input."""
    normalized = " ".join(text.split())
    lowered = normalized.lower()

    category = next(
        (
            name
            for name, words in _CATEGORY_WORDS.items()
            if any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in words)
        ),
        None,
    )
    use_cases = [
        name
        for name, words in _USE_CASES.items()
        if any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in words)
    ]
    excluded = [
        brand
        for brand in _EXCLUDED_BRAND.findall(normalized)
        if not _feature_word(brand) and brand.lower() not in {"i", "the", "a", "more", "less"}
    ]

    owned = [
        item.strip()
        for match in _OWNED.findall(normalized)
        for item in _OWNED_SPLIT.split(match)
        if item.strip()
    ]

    criteria: dict[tuple[str, Operator], Criterion] = {}
    unparsed: list[str] = []
    sentences = [s for s in re.split(r"(?<=[.!?\n])\s+", normalized) if s.strip()]
    for sentence in sentences:
        for clause in (c for c in _CLAUSE_SPLIT.split(sentence) if c and c.strip()):
            found = _quantity_criteria(clause, sentence) + _feature_criteria(clause, sentence)
            for criterion in found:
                criteria.setdefault((criterion.key, criterion.operator), criterion)
            recognised = (
                found
                or _BUDGET_BARE.search(clause)
                or any(brand in clause for brand in excluded)
                or _OWNED.search(clause)
                or any(
                    re.search(rf"\b{re.escape(w)}\b", clause.lower())
                    for words in (*_CATEGORY_WORDS.values(), *_USE_CASES.values())
                    for w in words
                )
            )
            if not recognised:
                unparsed.append(clause.strip())

    spec = RequirementSpec(
        category=category,
        budget=_extract_budget(normalized),
        criteria=list(criteria.values()),
        excluded_brands=excluded,
        use_cases=use_cases,
        owned_devices=owned,
    )
    return RuleExtraction(spec=spec, unparsed=unparsed)
