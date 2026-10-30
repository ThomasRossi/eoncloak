"""Entity types and the registry that keeps pseudonyms consistent."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

import phonenumbers

DEFAULT_LABEL_FORMAT = "[{type}_{n}]"
MAPPING_VERSION = 1


class EntityType(StrEnum):
    PERSON = "PERSON"
    ORG = "ORG"
    ADDRESS = "ADDRESS"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    IBAN = "IBAN"
    ID = "ID"
    URL = "URL"
    BIRTH_DATE = "BIRTH_DATE"


ALL_TYPES: tuple[EntityType, ...] = tuple(EntityType)


@dataclass(frozen=True)
class Mention:
    """A piece of text that a detector flagged as personal data."""

    text: str
    type: EntityType
    canonical: str | None = None


@dataclass
class Entity:
    """One real-world person/organisation/address/... and every way it is written."""

    type: EntityType
    canonical: str
    label: str
    aliases: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "type": self.type.value,
            "canonical": self.canonical,
            "aliases": sorted(self.aliases),
        }


_EDGE_PUNCT = " \t\r\n.,;:!?\"'()[]{}<>«»“”\u2018\u2019"


def _min_word_chars(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


# Legal forms that may be left out when a company is mentioned again ("ACME Ltd" -> "ACME").
# fmt: off
_LEGAL_FORMS = [
    "limited", "ltd", "llc", "llp", "plc", "inc", "incorporated", "corp", "corporation",
    "gmbh", "ag", "kg", "sa", "sas", "sarl", "srl", "spa", "sagl", "bv", "nv", "oy", "oyj",
    "ab", "as", "a/s", "aps", "sl", "pty", "pte",
]
# fmt: on
_LEGAL_SUFFIX_RE = re.compile(
    r"(?:[\s,]+(?:"
    + "|".join(r"\.?".join(map(re.escape, form)) for form in _LEGAL_FORMS)
    + r")\.?)+$",
    re.IGNORECASE,
)


def alias_variants(etype: EntityType, surface: str) -> list[str]:
    """Shorter ways of writing the same entity that detectors often miss.

    "NORTHWIND LTD." also appears as "NORTHWIND LTD," and "BLUE HERON LTD" as "BLUE HERON".
    """
    if etype in (EntityType.EMAIL, EntityType.URL, EntityType.PHONE, EntityType.IBAN):
        return []
    variants = []
    if surface.endswith("."):
        variants.append(surface.rstrip(". "))
    if etype is EntityType.ORG:
        base = _LEGAL_SUFFIX_RE.sub("", surface).rstrip(" ,&")
        if base != surface and _min_word_chars(base) >= 3:
            variants.append(base)
    return [v for v in dict.fromkeys(variants) if v != surface and _min_word_chars(v) >= 2]


class Registry:
    """Maps every surface form to a single entity so repeated mentions share one label.

    The registry can be serialised to a mapping file and loaded back, which keeps labels
    consistent across several documents and makes ``restore`` possible.
    """

    def __init__(self, label_format: str = DEFAULT_LABEL_FORMAT, phone_region: str | None = None):
        self.label_format = label_format
        self.phone_region = phone_region
        self.entities: list[Entity] = []
        self._by_key: dict[str, Entity] = {}
        self._counters: dict[EntityType, int] = {}

    # -- normalisation -----------------------------------------------------------------

    def key(self, etype: EntityType, text: str) -> str:
        text = unicodedata.normalize("NFKC", text).strip(_EDGE_PUNCT)
        if etype is EntityType.PHONE:
            try:
                number = phonenumbers.parse(text, self.phone_region)
                return "phone:" + phonenumbers.format_number(
                    number, phonenumbers.PhoneNumberFormat.E164
                )
            except phonenumbers.NumberParseException:
                return "phone:" + re.sub(r"\D", "", text)
        if etype is EntityType.IBAN:
            return "iban:" + re.sub(r"\s", "", text).upper()
        if etype is EntityType.URL:
            # http/https, "www." and a trailing slash don't make it a different link.
            url = re.sub(r"^(?:[a-z]+://)?(?:www\.)?", "", re.sub(r"\s", "", text), flags=re.I)
            return "url:" + url.rstrip("/").casefold()
        return re.sub(r"\s+", " ", text).casefold()

    # -- building ----------------------------------------------------------------------

    def add(self, mention: Mention) -> Entity | None:
        surface = mention.text.strip()
        if _min_word_chars(surface) < 2:
            return None
        key = self.key(mention.type, surface)
        entity = self._by_key.get(key)
        if entity is None and mention.canonical:
            entity = self._by_key.get(self.key(mention.type, mention.canonical))
        if entity is None:
            entity = self._new_entity(mention.type, (mention.canonical or surface).strip())
        self._by_key.setdefault(key, entity)
        entity.aliases.add(surface)
        for variant in alias_variants(mention.type, surface):
            # A variant never takes over a form that already belongs to another entity.
            if self._by_key.setdefault(self.key(mention.type, variant), entity) is entity:
                entity.aliases.add(variant)
        return entity

    def add_all(self, mentions: Iterable[Mention]) -> None:
        for mention in mentions:
            self.add(mention)

    def _new_entity(self, etype: EntityType, canonical: str) -> Entity:
        n = self._counters.get(etype, 0) + 1
        self._counters[etype] = n
        entity = Entity(etype, canonical, self.label_format.format(type=etype.value, n=n))
        self.entities.append(entity)
        self._by_key.setdefault(self.key(etype, canonical), entity)
        if _min_word_chars(canonical) >= 2:
            entity.aliases.add(canonical)
        return entity

    def known(self) -> list[tuple[EntityType, str]]:
        """Entities found so far, as (type, canonical) pairs, for prompting the LLM."""
        return [(e.type, e.canonical) for e in self.entities if e.type is not EntityType.URL]

    def replacements(self) -> dict[str, str]:
        """Every surface form mapped to its label."""
        return {alias: e.label for e in self.entities for alias in e.aliases}

    # -- mapping files -----------------------------------------------------------------

    def to_mapping(self) -> dict:
        return {
            "version": MAPPING_VERSION,
            "label_format": self.label_format,
            "entities": [e.to_dict() for e in self.entities],
        }

    @classmethod
    def from_mapping(cls, data: dict, phone_region: str | None = None) -> Registry:
        if data.get("version") != MAPPING_VERSION:
            raise ValueError(f"Unsupported mapping file version: {data.get('version')!r}")
        registry = cls(data.get("label_format", DEFAULT_LABEL_FORMAT), phone_region)
        for item in data.get("entities", []):
            etype = EntityType(item["type"])
            entity = Entity(etype, item["canonical"], item["label"], set(item.get("aliases", [])))
            registry.entities.append(entity)
            for alias in entity.aliases | {entity.canonical}:
                registry._by_key.setdefault(registry.key(etype, alias), entity)
            n = _label_number(registry.label_format, etype, entity.label)
            if n is not None:
                registry._counters[etype] = max(registry._counters.get(etype, 0), n)
        return registry


def _label_number(label_format: str, etype: EntityType, label: str) -> int | None:
    pattern = re.escape(label_format).replace(re.escape("{type}"), re.escape(etype.value))
    match = re.fullmatch(pattern.replace(re.escape("{n}"), r"(\d+)"), label)
    return int(match.group(1)) if match else None
