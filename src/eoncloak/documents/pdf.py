from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader

from .text import TextDocument

log = logging.getLogger(__name__)


class PdfDocument(TextDocument):
    """Text extracted from a PDF. Output is plain text or DOCX; the PDF is not re-rendered."""

    def __init__(self, content: str, empty_pages: list[int]):
        super().__init__(content)
        self.empty_pages = empty_pages

    @classmethod
    def load(cls, path: Path) -> PdfDocument:
        reader = PdfReader(path)
        pages, empty = [], []
        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if not text.strip():
                empty.append(number)
            pages.append(text)
        if empty:
            log.warning(
                "%s: no text on page(s) %s; scanned pages need OCR, which is not supported yet",
                path,
                ", ".join(map(str, empty)),
            )
        return cls("\n\n".join(pages), empty)
