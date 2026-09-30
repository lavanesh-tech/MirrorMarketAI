from __future__ import annotations

import pytest

from app.ingestion.parsers import (
    ParseError,
    normalize_text,
    parse_document,
    parse_html,
    sniff_content_type,
)
from tests.support.documents import SPEC_PAGE_HTML, make_pdf

pytestmark = pytest.mark.unit


def test_html_extraction_drops_scripts_and_styles() -> None:
    doc = parse_html(SPEC_PAGE_HTML.encode())
    assert doc.title == "Acme Laptop 14 \u2013 Tech Specs"
    assert "Memory: 16 GB LPDDR5" in doc.text
    assert "Battery: 70 Wh" in doc.text
    assert "cookie" not in doc.text
    assert "color: red" not in doc.text
    assert "Enable JS" not in doc.text
    assert "Zerowidth hidden" in doc.text  # zero-width char removed


def test_normalize_text_collapses_whitespace_and_controls() -> None:
    raw = "  Line\u00a0one \t\t with  spaces\r\n\n\n\nLine two\x00\x07  "
    assert normalize_text(raw) == "Line one with spaces\n\nLine two"


def test_pdf_extraction() -> None:
    pdf = make_pdf(["Warranty: 1 year limited", "Returns: 14 days"], title="Warranty Terms")
    doc = parse_document(pdf, "application/pdf", max_pdf_pages=10)
    assert doc.parser == "pdf"
    assert doc.title == "Warranty Terms"
    assert "Warranty: 1 year limited" in doc.text
    assert "Returns: 14 days" in doc.text


def test_pdf_page_limit() -> None:
    pdf = make_pdf(["page"], pages=3)
    with pytest.raises(ParseError, match="more than 2 pages"):
        parse_document(pdf, "application/pdf", max_pdf_pages=2)


def test_corrupt_pdf_is_rejected() -> None:
    with pytest.raises(ParseError):
        parse_document(b"%PDF-1.7\n garbage", "application/pdf", max_pdf_pages=10)


def test_empty_document_is_rejected() -> None:
    with pytest.raises(ParseError, match="no text"):
        parse_document(b"<html><script>x</script></html>", "text/html", max_pdf_pages=10)


def test_unsupported_type_is_rejected() -> None:
    with pytest.raises(ParseError):
        parse_document(b"x", "image/png", max_pdf_pages=10)


@pytest.mark.parametrize(
    ("content", "declared", "expected"),
    [
        (b"%PDF-1.4 ...", "text/plain", "application/pdf"),  # magic bytes beat the claim
        (b"<!DOCTYPE html><html></html>", "application/pdf", "text/html"),
        (b"# Title\nbody", "text/markdown", "text/markdown"),
        (b"plain words", None, "text/plain"),
    ],
)
def test_sniff_content_type(content: bytes, declared: str | None, expected: str) -> None:
    assert sniff_content_type(content, declared) == expected


def test_sniff_rejects_other_binaries() -> None:
    with pytest.raises(ParseError, match="binary"):
        sniff_content_type(b"\x89PNG\r\n\x1a\n\x00\x00\x00", "image/png")


def test_latin1_text_is_decoded() -> None:
    doc = parse_document("Caf\u00e9 menu".encode("cp1252"), "text/plain", max_pdf_pages=1)
    assert doc.text == "Caf\u00e9 menu"
