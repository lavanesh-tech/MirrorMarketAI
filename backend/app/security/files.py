"""Upload hygiene: safe filenames and refusing dangerous content.

Uploaded bytes are stored and parsed, never executed or served back inline, which
already removes most risk. These checks close the rest:
- the type is decided from the bytes (see `parsers.sniff_content_type`), and
  known executable/archive signatures are rejected with a clear reason;
- PDFs with active content (JavaScript, launch actions, embedded files) are refused;
- filenames are reduced to a harmless display name (no paths, control characters,
  leading dots or reserved device names), because they end up in logs and UIs.
"""

from __future__ import annotations

import re
import unicodedata

MAX_FILENAME_CHARS = 120
DEFAULT_FILENAME = "upload"

_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x7fELF", "Linux executable"),
    (b"\xcf\xfa\xed\xfe", "macOS executable"),
    (b"\xca\xfe\xba\xbe", "macOS/Java binary"),
    (b"PK\x03\x04", "ZIP archive (including Office documents)"),
    (b"\x1f\x8b", "gzip archive"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip archive"),
    (b"Rar!\x1a\x07", "RAR archive"),
    (b"#!/", "script"),
)
_PDF_ACTIVE = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/RichMedia", b"/XFA")
_PDF_NAME = re.compile(rb"/[A-Za-z]+")
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.I)
_UNSAFE = re.compile(r"[^\w.\- ()]+")


class UnsafeFileError(ValueError):
    """The upload is refused; the message is safe to show to the user."""


def sanitize_filename(name: str | None) -> str:
    """A display-safe file name. Never use it as a filesystem path."""
    name = unicodedata.normalize("NFKC", name or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]  # drop any directory part
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = _UNSAFE.sub("_", name).strip(" .")
    if not name or _RESERVED.match(name):
        return DEFAULT_FILENAME
    if len(name) > MAX_FILENAME_CHARS:
        stem, dot, extension = name.rpartition(".")
        keep = extension[:10] if dot and stem else ""
        name = name[: MAX_FILENAME_CHARS - len(keep) - 1].rstrip(" .") + (
            f".{keep}" if keep else ""
        )
    return name


def check_upload(content: bytes) -> None:
    """Raise UnsafeFileError for executables, archives and PDFs with active content."""
    if not content.strip():
        raise UnsafeFileError("the file is empty")
    if content.startswith(b"MZ") and b"\x00" in content[:512]:  # "MZ" alone could be text
        raise UnsafeFileError("Windows executable files are not accepted")
    for signature, label in _SIGNATURES:
        if content.startswith(signature):
            raise UnsafeFileError(f"{label} files are not accepted")
    if content.startswith(b"%PDF-"):
        names = set(_PDF_NAME.findall(content))
        active = sorted(n.decode() for n in _PDF_ACTIVE if n in names)
        if active:
            raise UnsafeFileError(
                "PDFs with active content are not accepted (" + ", ".join(active) + ")"
            )
