"""Turn raw source bytes into normalized plain text.

All content parsed here is UNTRUSTED DATA. It is stored and later retrieved as
evidence, but it is never executed and never treated as instructions (prompt
injection defenses are built on this in Phases 9 and 22).
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_TEXT_CHARS = 1_000_000

_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "object"})
_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
     "header", "footer", "table", "ul", "ol", "dt", "dd", "pre", "blockquote"}
)  # fmt: skip
_CONTROL_CHARS = re.compile(
    "[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f\\u200b-\\u200f\\u2028\\u2029\\ufeff]"
)
_SPACES = re.compile("[ \\t\\u00a0]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


class ParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    title: str | None
    text: str
    parser: str


def normalize_text(text: str) -> str:
    """NFKC, strip control/zero-width chars (used to hide injected text), collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_CHARS.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    text = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    return text[:MAX_TEXT_CHARS]


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.parts.append(data)


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")  # never fails


def parse_html(content: bytes) -> ParsedDocument:
    extractor = _TextExtractor()
    extractor.feed(_decode(content))
    extractor.close()
    title = normalize_text("".join(extractor.title_parts)) or None
    return ParsedDocument(title=title, text=normalize_text("".join(extractor.parts)), parser="html")


def parse_text(content: bytes) -> ParsedDocument:
    return ParsedDocument(title=None, text=normalize_text(_decode(content)), parser="text")


def parse_pdf(content: bytes, max_pages: int) -> ParsedDocument:
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ParseError("encrypted PDFs are not supported")
        if len(reader.pages) > max_pages:
            raise ParseError(f"PDF has more than {max_pages} pages")
        pages = [page.extract_text() or "" for page in reader.pages]
        raw_title = reader.metadata.title if reader.metadata else None
    except PdfReadError as exc:
        raise ParseError("file is not a readable PDF") from exc
    title = normalize_text(str(raw_title)) if raw_title else None
    return ParsedDocument(
        title=title or None, text=normalize_text("\n\n".join(pages)), parser="pdf"
    )


def sniff_content_type(content: bytes, declared: str | None) -> str:
    """Decide the real type from magic bytes; never trust a client's claim alone."""
    head = content[:1024].lstrip().lower()
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith((b"<!doctype html", b"<html")) or b"<html" in head:
        return "text/html"
    if b"\x00" in content[:8192]:
        raise ParseError("binary files other than PDF are not supported")
    if declared in {"text/markdown", "text/x-markdown"}:
        return "text/markdown"
    return "text/plain"


def parse_document(content: bytes, content_type: str, *, max_pdf_pages: int) -> ParsedDocument:
    if content_type == "application/pdf":
        parsed = parse_pdf(content, max_pdf_pages)
    elif content_type in {"text/html", "application/xhtml+xml"}:
        parsed = parse_html(content)
    elif content_type in {"text/plain", "text/markdown"}:
        parsed = parse_text(content)
    else:
        raise ParseError(f"unsupported content type {content_type}")
    if not parsed.text:
        raise ParseError("no text could be extracted")
    return parsed
