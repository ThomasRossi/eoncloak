"""Deterministic detectors for data with a fixed shape: emails, phone numbers, IBANs.

They run without any LLM, so they also back the ``--no-llm`` offline mode.
"""

from __future__ import annotations

import re
from collections.abc import Collection

import phonenumbers

from .entities import EntityType, Mention

EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+%-]+@[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}(?![\w-])")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")


def detect_rules(
    text: str, types: Collection[EntityType], phone_region: str | None = None
) -> list[Mention]:
    mentions: list[Mention] = []
    if EntityType.EMAIL in types:
        mentions += [Mention(m.group(0), EntityType.EMAIL) for m in EMAIL_RE.finditer(text)]
    if EntityType.PHONE in types:
        mentions += _phones(text, phone_region)
    if EntityType.IBAN in types:
        mentions += [
            Mention(m.group(0), EntityType.IBAN)
            for m in IBAN_RE.finditer(text)
            if is_valid_iban(m.group(0))
        ]
    return mentions


def _phones(text: str, region: str | None) -> list[Mention]:
    # Without a region only numbers in international format (+39 ...) can be recognised;
    # the LLM detector covers local formats when no region is known.
    found = []
    for match in phonenumbers.PhoneNumberMatcher(text, region or "ZZ"):
        found.append(Mention(match.raw_string, EntityType.PHONE))
    return found


def is_valid_iban(candidate: str) -> bool:
    iban = candidate.replace(" ", "").upper()
    if not 15 <= len(iban) <= 34:
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1
