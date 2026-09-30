"""Pure product-catalog rules: identifier validation and canonical keys.

Deterministic code (not an LLM) decides whether two records are the same
product and whether an identifier is well-formed.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum


class IdentifierScheme(StrEnum):
    GTIN = "GTIN"  # GTIN-8/12/13/14 (covers UPC-A = GTIN-12 and EAN-13 = GTIN-13)
    MPN = "MPN"  # manufacturer part number
    ASIN = "ASIN"  # Amazon Standard Identification Number
    SKU = "SKU"  # retailer/manufacturer stock-keeping unit


class InvalidIdentifierError(ValueError):
    pass


_GTIN_LENGTHS = frozenset({8, 12, 13, 14})
_ASIN = re.compile(r"^[A-Z0-9]{10}$")
_FREEFORM = re.compile(r"^[A-Z0-9][A-Z0-9._/\-]{0,63}$")
_SPEC_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_WHITESPACE = re.compile(r"\s+")


def gtin_check_digit_valid(digits: str) -> bool:
    """GS1 mod-10 check: weights 3,1,3,1... from the rightmost data digit."""
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def normalize_identifier(scheme: IdentifierScheme, raw: str) -> str:
    """Return the canonical form of an identifier or raise InvalidIdentifierError."""
    value = raw.strip().upper()
    if scheme is IdentifierScheme.GTIN:
        value = value.replace(" ", "").replace("-", "")
        if not value.isdigit() or len(value) not in _GTIN_LENGTHS:
            raise InvalidIdentifierError("GTIN must be 8, 12, 13 or 14 digits")
        if not gtin_check_digit_valid(value):
            raise InvalidIdentifierError("GTIN check digit is invalid")
        return value.zfill(14)  # store as GTIN-14 so UPC-A and EAN-13 forms compare equal
    if scheme is IdentifierScheme.ASIN:
        if not _ASIN.fullmatch(value):
            raise InvalidIdentifierError("ASIN must be 10 letters/digits")
        return value
    value = _WHITESPACE.sub("", value)
    if not _FREEFORM.fullmatch(value):
        raise InvalidIdentifierError(f"{scheme.value} contains invalid characters or is too long")
    return value


def _normalize_text(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return _WHITESPACE.sub(" ", folded).strip()


def canonical_product_key(brand: str, name: str) -> str:
    """Case/spacing-insensitive key: ('Apple', 'MacBook  Air') == ('apple', 'macbook air')."""
    return f"{_normalize_text(brand)}::{_normalize_text(name)}"


def is_valid_spec_key(key: str) -> bool:
    """Spec keys are snake_case identifiers, e.g. `ram_gb`, `battery_wh`, `ports_usb_c`."""
    return bool(_SPEC_KEY.fullmatch(key))
