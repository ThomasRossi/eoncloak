"""Deterministic detectors for data with a fixed shape: emails, links, phone numbers, IBANs,
VAT numbers and identifiers written after a label such as "Company no.:".

They run without any LLM, so they also back the ``--no-llm`` offline mode.
"""

from __future__ import annotations

import re
from collections.abc import Collection

import phonenumbers

from .entities import EntityType, Mention

EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+%-]+@[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}(?![\w-])")
_URL_CHARS = r"[^\s<>\"'`]"
# A link wrapped at a hyphen by PDF extraction continues on the next line: "company-\ninfo...".
_URL_TAIL = rf"(?:-\r?\n(?={_URL_CHARS})|{_URL_CHARS})*"
_TLDS = (
    "com|net|org|info|biz|io|ai|co|app|dev|online|shop|store|site|tech|me|tv|eu|uk|it|de|fr|"
    "es|pt|nl|be|ch|at|ie|lu|pl|cz|sk|hu|ro|bg|gr|hr|si|se|no|dk|fi|ee|lv|lt|tr|ru|ua|us|ca|"
    "mx|br|ar|cl|au|nz|jp|cn|hk|sg|in|il|ae|za|gov|edu"
)
URL_RE = re.compile(
    rf"(?:\b(?:https?|ftp)://|\bwww\.){_URL_TAIL}"
    # Bare domains need a known top-level domain, so "S.p.A." or "report.pdf" don't match.
    rf"|(?<![@\w.-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:{_TLDS})\b(?![@-])(?:/{_URL_TAIL})?",
    re.IGNORECASE,
)
# VAT numbers with their country prefix (EU, UK, Switzerland, Norway).
VAT_RE = re.compile(
    r"\b(?:ATU\d{8}|BE[01]\d{9}|BG\d{9,10}|CY\d{8}[A-Z]|CZ\d{8,10}|DE\d{9}|DK\d{8}|EE\d{9}|"
    r"EL\d{9}|ES[A-Z0-9]\d{7}[A-Z0-9]|FI\d{8}|FR[A-HJ-NP-Z0-9]{2}\d{9}|GB(?:\d{9}|\d{12})|"
    r"HR\d{11}|HU\d{8}|IE\d{7}[A-W][A-I]?|IT\d{11}|LT(?:\d{9}|\d{12})|LU\d{8}|LV\d{11}|"
    r"MT\d{8}|NL\d{9}B\d{2}|PL\d{10}|PT\d{9}|RO\d{2,10}|SE\d{12}|SI\d{8}|SK\d{10}|"
    r"CHE-?\d{3}\.?\d{3}\.?\d{3}|NO\d{9}(?:MVA)?)\b"
)
# Identifiers announced by a label, in the languages eoncloak users write in most.
_ID_LABELS = (
    r"company\s+(?:no|number|reg(?:istration)?(?:\s+(?:no|number))?)|registration\s+(?:no|number)|"
    r"reg\.?\s+no|vat\s+(?:no|number|reg(?:istration)?(?:\s+(?:no|number))?)|vat\s+id|"
    r"tax\s+(?:id|no|number|code)|"
    r"p\.?\s?iva|partita\s+iva|codice\s+fiscale|c\.f|numero\s+rea|"
    r"ust-?id(?:nr)?|steuer-?(?:nr|nummer)|handelsregister(?:nummer)?|"
    r"tva(?:\s+intracom(?:munautaire)?)?|n°\s+tva|kvk(?:-?nummer)?|btw(?:-?nummer)?|"
    r"uid(?:-?nr)?"
)
# Acronyms count only in capitals: "EIN" is a label, German "ein" is not.
_ID_ACRONYMS = r"CRN|EIN|UTR|REA|HRA|HRB|SIREN|SIRET|RCS|CIF|NIF|NIE|NIPC"
# Generic "No." / "Nr." / "n°" only with their dot or degree sign, so the word "no" is ignored.
_GENERIC_LABELS = r"\b(?:no|nr)\.|\bn[°º]"
LABELLED_ID_RE = re.compile(
    rf"(?:(?i:\b(?:{_ID_LABELS})\b\.?|{_GENERIC_LABELS})|\b(?:{_ID_ACRONYMS})\b)\s*[:#.]?\s*"
    # Letters only in upper case, digit groups may be separated by single spaces.
    r"(?P<id>[A-Z]{0,4}[ -]?\d(?:[\dA-Z./-]|(?<=\d) (?=\d)){3,}[\dA-Z])"
)
# Italian codice fiscale.
CODICE_FISCALE_RE = re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPR-T]\d{2}[A-Z]\d{3}[A-Z]\b")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")


def detect_rules(
    text: str, types: Collection[EntityType], phone_region: str | None = None
) -> list[Mention]:
    mentions: list[Mention] = []
    if EntityType.EMAIL in types:
        mentions += [Mention(m.group(0), EntityType.EMAIL) for m in EMAIL_RE.finditer(text)]
    if EntityType.URL in types:
        mentions += [Mention(url, EntityType.URL) for url in _urls(text)]
    if EntityType.ID in types:
        for pattern in (VAT_RE, CODICE_FISCALE_RE):
            mentions += [Mention(m.group(0), EntityType.ID) for m in pattern.finditer(text)]
        mentions += [
            Mention(m.group("id"), EntityType.ID)
            for m in LABELLED_ID_RE.finditer(text)
            if sum(ch.isdigit() for ch in m.group("id")) >= 5
        ]
    if EntityType.PHONE in types:
        mentions += _phones(text, phone_region)
    if EntityType.IBAN in types:
        mentions += [
            Mention(m.group(0), EntityType.IBAN)
            for m in IBAN_RE.finditer(text)
            if is_valid_iban(m.group(0))
        ]
    return mentions


def _urls(text: str) -> list[str]:
    urls = []
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?'\"")
        while url.endswith((")", "]")) and url.count(url[-1]) > url.count(
            "(" if url[-1] == ")" else "["
        ):
            url = url[:-1].rstrip(".,;:!?")
        urls.append(url)
    return urls


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
