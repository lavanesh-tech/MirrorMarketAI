from __future__ import annotations

import pytest

from app.domain.products import (
    IdentifierScheme,
    InvalidIdentifierError,
    canonical_product_key,
    gtin_check_digit_valid,
    is_valid_spec_key,
    normalize_identifier,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("gtin", ["96385074", "036000291452", "4006381333931", "10614141000415"])
def test_valid_gtin_check_digits(gtin: str) -> None:
    assert gtin_check_digit_valid(gtin)


@pytest.mark.parametrize("gtin", ["96385075", "036000291453", "4006381333932"])
def test_invalid_gtin_check_digits(gtin: str) -> None:
    assert not gtin_check_digit_valid(gtin)


def test_upc_and_ean_forms_of_same_gtin_normalize_equal() -> None:
    upc = normalize_identifier(IdentifierScheme.GTIN, "0 36000 29145 2")
    ean = normalize_identifier(IdentifierScheme.GTIN, "0036000291452")
    assert upc == ean == "00036000291452"


@pytest.mark.parametrize(
    ("scheme", "raw"),
    [
        (IdentifierScheme.GTIN, "12345"),
        (IdentifierScheme.GTIN, "abcdefghijkl"),
        (IdentifierScheme.GTIN, "4006381333932"),
        (IdentifierScheme.ASIN, "B08N5WRWN"),
        (IdentifierScheme.ASIN, "B08N5WRWN!!"),
        (IdentifierScheme.MPN, "MX2D3LL/A <script>"),
        (IdentifierScheme.SKU, "x" * 65),
    ],
)
def test_invalid_identifiers_raise(scheme: IdentifierScheme, raw: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        normalize_identifier(scheme, raw)


@pytest.mark.parametrize(
    ("scheme", "raw", "expected"),
    [
        (IdentifierScheme.ASIN, " b08n5wrwnw ", "B08N5WRWNW"),
        (IdentifierScheme.MPN, "mx2d3ll/a", "MX2D3LL/A"),
        (IdentifierScheme.SKU, "ab-12 34", "AB-1234"),
    ],
)
def test_identifier_normalization(scheme: IdentifierScheme, raw: str, expected: str) -> None:
    assert normalize_identifier(scheme, raw) == expected


def test_canonical_key_ignores_case_whitespace_and_width() -> None:
    assert canonical_product_key("Apple", "MacBook  Air 13") == canonical_product_key(
        " apple ", "macbook air 13"
    )
    fullwidth_asus = "\uff21\uff33\uff35\uff33"  # NFKC folds full-width letters to ASCII
    assert canonical_product_key(fullwidth_asus, "Zenbook") == canonical_product_key(
        "asus", "zenbook"
    )
    assert canonical_product_key("Dell", "XPS 13") != canonical_product_key("Dell", "XPS 15")


@pytest.mark.parametrize(
    ("key", "ok"),
    [("ram_gb", True), ("battery_wh", True), ("RAM", False), ("1ram", False), ("ram-gb", False)],
)
def test_spec_key_format(key: str, ok: bool) -> None:
    assert is_valid_spec_key(key) is ok
