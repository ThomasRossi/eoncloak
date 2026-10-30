"""LLM-based detection of names, organisations, addresses and identifiers."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass

from pydantic import BaseModel

from .entities import EntityType, Mention
from .llm import JSONModel

DEFAULT_LANGUAGE = "en"
LANGUAGE_SAMPLE_CHARS = 3000

TYPE_GUIDE = {
    EntityType.PERSON: (
        "names of people: full names, first names alone, surnames alone, initials with "
        "surname, nicknames. Exclude titles and honorifics (Mr., Dr., Avv., Sig.ra, ...)."
    ),
    EntityType.ORG: (
        "names of companies, firms, associations, institutions and other organisations, "
        "including legal suffixes as written (S.p.A., GmbH, Ltd, Inc.). Not product names."
    ),
    EntityType.ADDRESS: (
        "postal or street addresses, as the full span written in the text (street, number, "
        "postcode, city). Not standalone city, region or country names."
    ),
    EntityType.EMAIL: "email addresses.",
    EntityType.PHONE: "telephone and fax numbers in any format.",
    EntityType.IBAN: "IBANs and bank account numbers.",
    EntityType.ID: (
        "identifiers of people AND of companies, i.e. anything that can be looked up in a "
        "register or database: company registration numbers (UK company number, REA, HRB, "
        "SIREN/SIRET, KvK, ...), VAT numbers and tax codes (partita IVA, codice fiscale, "
        "USt-IdNr, EIN, ...), national ID, social security, passport and driving licence "
        "numbers, licence plates, customer, account, officer, trader, case and file numbers, "
        "shipping and invoice references (bills of lading, container numbers). Just the "
        'identifier itself, without its label ("01234567", not "Company no.: 01234567").'
    ),
    EntityType.BIRTH_DATE: (
        "dates of birth, also partial ones: a month and year or a year alone given as when "
        'someone was born ("Born September 1961" -> "September 1961"). Just the date, '
        'without words like "born". Not other dates.'
    ),
}


class LanguageReply(BaseModel):
    language: str
    country: str | None


class EntityItem(BaseModel):
    text: str
    type: EntityType
    canonical: str


class EntitiesReply(BaseModel):
    entities: list[EntityItem]


@dataclass(frozen=True)
class LanguageInfo:
    language: str = DEFAULT_LANGUAGE
    country: str | None = None


def detect_language(llm: JSONModel, text: str) -> LanguageInfo:
    """Ask the LLM for the main language and country; fall back to English."""
    system = (
        "Identify the main language of the document and the country it most likely refers to "
        "(use addresses, phone formats, currency, legal terms). Reply with JSON: "
        '{"language": "<ISO 639-1 code>", "country": "<ISO 3166-1 alpha-2 code or null>"}.'
    )
    try:
        reply = llm.complete_json(system, text[:LANGUAGE_SAMPLE_CHARS], LanguageReply)
    except Exception:
        return LanguageInfo()
    language = reply.language.strip().lower()[:2] if reply.language.strip() else ""
    country = (reply.country or "").strip().upper()
    return LanguageInfo(
        language if re.fullmatch(r"[a-z]{2}", language) else DEFAULT_LANGUAGE,
        country if re.fullmatch(r"[A-Z]{2}", country) else None,
    )


def unique_lines(text: str) -> str:
    """``text`` without lines that already appeared earlier.

    Email threads repeat signatures, disclaimers and quoted headers many times. Lines are
    compared ignoring spacing, case and the ``>`` quote marks of plain-text replies. Only what
    the model reads is shortened: replacement still runs over the whole document.
    """
    seen: set[str] = set()
    lines: list[str] = []
    for line in text.splitlines():
        key = re.sub(r"\s+", " ", line.lstrip("> \t")).strip().casefold()
        if key in seen or (not key and lines and not lines[-1].strip()):
            continue
        if key:
            seen.add(key)
        lines.append(line)
    return "\n".join(lines)


def chunk_text(text: str, max_chars: int) -> Iterator[str]:
    """Split on line boundaries into chunks of at most ``max_chars`` (long lines on spaces)."""
    buf: list[str] = []
    size = 0
    for line in _split_long_lines(text.splitlines(keepends=True), max_chars):
        if size + len(line) > max_chars and buf:
            yield "".join(buf)
            buf, size = [], 0
        buf.append(line)
        size += len(line)
    if buf:
        yield "".join(buf)


def _split_long_lines(lines: list[str], max_chars: int) -> Iterator[str]:
    for line in lines:
        while len(line) > max_chars:
            cut = line.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            yield line[:cut]
            line = line[cut:]
        yield line


def _system_prompt(types: Collection[EntityType], language: str) -> str:
    guide = "\n".join(f"- {t.value}: {TYPE_GUIDE[t]}" for t in types)
    return f"""You find personal and identifying data in documents so they can be anonymised.
The document's main language is "{language}", but it may contain other languages.

Entity types:
{guide}

Rules:
- "text" must be copied exactly as it appears in the document (same spelling, case, accents).
- List every distinct way an entity is written, each as its own item: e.g. "Mario Rossi",
  "Rossi" and "M. Rossi" are three items.
- "canonical" is the fullest name of the same real-world entity. If it matches one of the
  KNOWN ENTITIES, copy that canonical exactly so the mentions stay linked.
- When in doubt, include it: a missed name is worse than an extra one.
- Identifiers of companies count too: a company number or VAT number leads straight to the
  people behind the company in public registers.
- Do not invent entities that are not in the text.

Reply with JSON only: {{"entities": [{{"text": "...", "type": "...", "canonical": "..."}}]}}"""


def _user_prompt(chunk: str, known: list[tuple[EntityType, str]]) -> str:
    known_json = json.dumps(
        [{"type": t.value, "canonical": c} for t, c in known], ensure_ascii=False
    )
    return f"KNOWN ENTITIES: {known_json}\n\nDOCUMENT:\n<<<\n{chunk}\n>>>"


def _appears_in(needle: str, haystack: str) -> bool:
    pattern = r"\s+".join(re.escape(part) for part in needle.split())
    return bool(pattern) and re.search(pattern, haystack, re.IGNORECASE) is not None


class LLMDetector:
    def __init__(
        self,
        llm: JSONModel,
        types: Collection[EntityType],
        chunk_chars: int = 6000,
        skip_repeated_lines: bool = True,
    ):
        self.llm = llm
        self.types = [t for t in TYPE_GUIDE if t in types]
        self.chunk_chars = chunk_chars
        self.skip_repeated_lines = skip_repeated_lines

    def detect(
        self,
        text: str,
        language: str,
        known: Callable[[], list[tuple[EntityType, str]]],
        on_mentions: Callable[[list[Mention]], None],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Detect chunk by chunk; ``on_mentions`` is called after each chunk so that the
        registry grows and later chunks can link aliases to entities already found."""
        if not self.types:
            return
        system = _system_prompt(self.types, language)
        if self.skip_repeated_lines:
            text = unique_lines(text)
        chunks = [c for c in chunk_text(text, self.chunk_chars) if c.strip()]
        for i, chunk in enumerate(chunks):
            reply = self.llm.complete_json(system, _user_prompt(chunk, known()), EntitiesReply)
            on_mentions(
                [
                    Mention(item.text, item.type, item.canonical or None)
                    for item in reply.entities
                    if item.type in self.types and _appears_in(item.text, chunk)
                ]
            )
            if on_progress:
                on_progress(i + 1, len(chunks))
