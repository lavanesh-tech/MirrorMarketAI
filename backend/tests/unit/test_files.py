from __future__ import annotations

import pytest

from app.security.files import UnsafeFileError, check_upload, sanitize_filename


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\spec sheet.txt", "spec sheet.txt"),
        ("a\x00b\r\nc.txt", "abc.txt"),
        ("<script>alert(1)</script>.html", "script_.html"),
        (".bashrc", "bashrc"),
        ("...", "upload"),
        ("", "upload"),
        (None, "upload"),
        ("CON", "upload"),
        ("nul.txt", "upload"),
        ("\uff52\uff45\uff50\uff4f\uff52\uff54.pdf", "report.pdf"),  # full-width letters
        ("résumé (final).pdf", "résumé (final).pdf"),
    ],
)
def test_sanitize_filename(raw: str | None, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_long_names_are_cut_but_keep_the_extension() -> None:
    name = sanitize_filename("a" * 300 + ".pdf")
    assert len(name) <= 120
    assert name.endswith(".pdf")
    assert len(sanitize_filename("b" * 300)) == 119


@pytest.mark.parametrize(
    "content",
    [
        b"Plain text about the MZ-500 laptop.",
        b"MZ is how this sentence starts, with no binary bytes.",
        b"# Markdown heading\n#!not-a-shebang",
        b"%PDF-1.7\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj /JSON /Launcher",
        b"<html><body>hi</body></html>",
    ],
)
def test_ordinary_documents_pass(content: bytes) -> None:
    check_upload(content)


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"", "empty"),
        (b"\x7fELF\x02\x01", "Linux executable"),
        (b"MZ\x90\x00", "Windows executable"),
        (b"\xcf\xfa\xed\xfe\x07", "macOS executable"),
        (b"PK\x03\x04", "ZIP"),
        (b"\x1f\x8b\x08", "gzip"),
        (b"7z\xbc\xaf\x27\x1c", "7-Zip"),
        (b"Rar!\x1a\x07\x00", "RAR"),
        (b"#!/usr/bin/env python", "script"),
        (b"%PDF-1.4 /Launch << /F (cmd.exe) >>", "/Launch"),
        (b"%PDF-1.4 /EmbeddedFile /JS", "/EmbeddedFile, /JS"),
    ],
)
def test_dangerous_content_is_refused(content: bytes, reason: str) -> None:
    with pytest.raises(UnsafeFileError, match=reason):
        check_upload(content)
