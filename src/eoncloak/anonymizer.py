"""The anonymisation pipeline: detect → link aliases → assign labels → rewrite → leak check."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings, check_local_only, load_settings
from .detect import DEFAULT_LANGUAGE, LanguageInfo, LLMDetector, detect_language
from .documents import (
    Document,
    TextDocument,
    default_output_suffix,
    load_document,
    save_document,
)
from .entities import Registry
from .llm import JSONModel, LLMClient
from .replace import Replacer
from .rules import detect_rules


@dataclass
class Result:
    language: LanguageInfo
    leaks: list[str] = field(default_factory=list)
    source: Path | None = None
    output: Path | None = None


class Anonymizer:
    """Anonymises documents; all documents handled by one instance share one registry, so the
    same person gets the same label everywhere.

    >>> anonymizer = Anonymizer(load_settings(provider="ollama", model="qwen3:14b"))
    >>> anonymizer.anonymize_text("Mario Rossi wrote to mario@rossi.it").text
    '[PERSON_1] wrote to [EMAIL_1]'
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        llm: JSONModel | None = None,
        registry: Registry | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ):
        self.settings = settings or load_settings()
        options = self.settings.anonymize
        check_local_only(self.settings)
        self.llm: JSONModel | None = None
        if options.use_llm:
            self.llm = llm or LLMClient(self.settings.llm)
        self.registry = registry or Registry(options.label_format)
        self.on_progress = on_progress

    # -- core --------------------------------------------------------------------------

    def _language(self, text: str) -> LanguageInfo:
        options = self.settings.anonymize
        if options.language != "auto":
            info = LanguageInfo(options.language)
        elif self.llm is not None and text.strip():
            info = detect_language(self.llm, text)
        else:
            info = LanguageInfo(DEFAULT_LANGUAGE)
        if options.region != "auto":
            info = LanguageInfo(info.language, options.region.upper())
        return info

    def anonymize_document(self, document: Document) -> Result:
        """Detect personal data in ``document`` and rewrite it in place."""
        options = self.settings.anonymize
        text = document.text()
        info = self._language(text)
        self.registry.phone_region = info.country
        types = set(options.entities)

        self.registry.add_all(detect_rules(text, types, info.country))
        if self.llm is not None:
            LLMDetector(self.llm, types, options.chunk_chars).detect(
                text, info.language, self.registry.known, self.registry.add_all, self.on_progress
            )

        replacer = self.replacer()
        document.apply(replacer, options.scrub_metadata)
        return Result(info, find_leaks(document.text(), replacer))

    def replacer(self) -> Replacer:
        return Replacer(self.registry.replacements())

    # -- conveniences --------------------------------------------------------------------

    def anonymize_text(self, text: str) -> TextResult:
        document = TextDocument(text)
        result = self.anonymize_document(document)
        return TextResult(result.language, result.leaks, text=document.text())

    def anonymize_file(self, source: Path, output: Path | None = None) -> Result:
        source = Path(source)
        output = Path(output) if output else default_output_path(source)
        if output.resolve() == source.resolve():
            raise ValueError(f"refusing to overwrite the input file {source}")
        document = load_document(source)
        result = self.anonymize_document(document)
        save_document(document, output)
        # Check what was actually written, not just the in-memory document.
        result.leaks = find_leaks(load_document(output).text(), self.replacer())
        result.source, result.output = source, output
        return result

    # -- mapping files -------------------------------------------------------------------

    def save_mapping(self, path: Path) -> None:
        save_mapping(self.registry, path)


@dataclass
class TextResult(Result):
    text: str = ""


def default_output_path(source: Path, suffix: str | None = None) -> Path:
    return source.with_name(f"{source.stem}.anon{suffix or default_output_suffix(source)}")


def find_leaks(text: str, replacer: Replacer) -> list[str]:
    """Original values still present in ``text``; empty when anonymisation is complete."""
    return sorted({text[m.start : m.end] for m in replacer.find(text)})


def save_mapping(registry: Registry, path: Path) -> None:
    """Write the mapping readable by the owner only: it contains the original personal data."""
    data = json.dumps(registry.to_mapping(), ensure_ascii=False, indent=2)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(data + "\n")


def load_mapping(path: Path, phone_region: str | None = None) -> Registry:
    with Path(path).open(encoding="utf-8") as fh:
        return Registry.from_mapping(json.load(fh), phone_region)


def restore_document(document: Document, registry: Registry) -> None:
    """Put the original values back (each label becomes the entity's canonical form)."""
    document.apply(
        Replacer({e.label: e.canonical for e in registry.entities}), scrub_metadata=False
    )
