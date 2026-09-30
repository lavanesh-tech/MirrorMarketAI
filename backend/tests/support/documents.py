"""Build small real documents (PDF/HTML) for parser and upload tests."""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def make_pdf(lines: list[str], *, pages: int = 1, title: str | None = None) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    if title:
        pdf.setTitle(title)
    for page in range(pages):
        y = 720
        for line in lines:
            pdf.drawString(72, y, f"{line}" if pages == 1 else f"[p{page + 1}] {line}")
            y -= 18
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


SPEC_PAGE_HTML = """<!doctype html>
<html><head><title>Acme Laptop 14 &ndash; Tech Specs</title>
<style>body { color: red }</style>
<script>window.steal = document.cookie;</script></head>
<body>
<nav>Home</nav>
<h1>Acme Laptop 14</h1>
<p>Memory: 16&nbsp;GB LPDDR5</p>
<ul><li>Battery: 70 Wh</li><li>Weight: 1.3 kg</li></ul>
<p>Zero\u200bwidth hidden</p>
<noscript>Enable JS</noscript>
</body></html>"""
