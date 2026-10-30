"""Loading and saving documents.

Every document exposes its text for detection, rewrites itself with a ``Replacer`` and can
be written either in its own format or as plain text / DOCX.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..replace import Replacer
from .pdf import PdfDocument
from .text import TextDocument, write_text
from .word import DocxDocument, write_docx_from_text


class Document(Protocol):
    def text(self) -> str:
        """All text that may hold personal data, including metadata; used for detection."""
        ...

    def plain_text(self) -> str:
        """The readable content, used when converting to another format."""
        ...

    def apply(self, replacer: Replacer, scrub_metadata: bool = True) -> None: ...


OUTPUT_SUFFIXES = {".txt", ".md", ".docx"}


class UnsupportedFormat(Exception):
    pass


def load_document(path: Path) -> Document:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return DocxDocument.load(path)
    if suffix == ".pdf":
        return PdfDocument.load(path)
    if suffix == ".doc":
        raise UnsupportedFormat(f"{path}: legacy .doc is not supported, save it as .docx first")
    return TextDocument.load(path)


def default_output_suffix(source: Path) -> str:
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        return ".txt"
    return suffix if suffix in OUTPUT_SUFFIXES else ".txt"


def save_document(document: Document, path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        if isinstance(document, DocxDocument):
            document.save(path)
        else:
            write_docx_from_text(document.plain_text(), path)
    else:
        write_text(document.plain_text(), path)


__all__ = [
    "Document",
    "DocxDocument",
    "PdfDocument",
    "TextDocument",
    "UnsupportedFormat",
    "default_output_suffix",
    "load_document",
    "save_document",
]
