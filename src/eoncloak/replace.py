"""Find every occurrence of known surface forms and swap them for their labels."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

# Aliases shorter than this are matched case-sensitively, so that e.g. the surname "Al"
# does not swallow the Italian/Spanish word "al".
CASE_INSENSITIVE_MIN_LEN = 4

# Scripts written without spaces between words: a word boundary would prevent matches.
_UNSPACED_RANGES = (
    (0x0E00, 0x0EFF),  # Thai, Lao
    (0x1000, 0x109F),  # Myanmar
    (0x1780, 0x17FF),  # Khmer
    (0x2E80, 0x9FFF),  # CJK radicals, kana, CJK ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0x20000, 0x3FFFF),  # CJK extensions
)


def _needs_boundary(ch: str) -> bool:
    if not ch.isalnum():
        return False
    code = ord(ch)
    return not any(lo <= code <= hi for lo, hi in _UNSPACED_RANGES)


def _alias_pattern(alias: str) -> str:
    body = r"\s+".join(re.escape(part) for part in alias.split())
    if len(alias) >= CASE_INSENSITIVE_MIN_LEN:
        body = f"(?i:{body})"
    if _needs_boundary(alias[0]):
        body = r"(?<!\w)" + body
    if _needs_boundary(alias[-1]):
        body += r"(?!\w)"
    return body


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    replacement: str


class Replacer:
    """Replaces surface forms with labels; longer forms win over the shorter ones they contain."""

    def __init__(self, replacements: Mapping[str, str]):
        cleaned = {alias.strip(): label for alias, label in replacements.items() if alias.strip()}
        self._lookup = {_lookup_key(alias): label for alias, label in cleaned.items()}
        aliases = sorted(cleaned, key=len, reverse=True)
        self._pattern = (
            re.compile("|".join(_alias_pattern(a) for a in aliases)) if aliases else None
        )

    def __bool__(self) -> bool:
        return self._pattern is not None

    def find(self, text: str) -> list[Match]:
        if self._pattern is None:
            return []
        matches = []
        for m in self._pattern.finditer(text):
            label = self._lookup.get(_lookup_key(m.group(0)))
            if label is not None:
                matches.append(Match(m.start(), m.end(), label))
        return matches

    def sub(self, text: str) -> str:
        return apply_matches(text, self.find(text))


def _lookup_key(text: str) -> str:
    return " ".join(text.split()).casefold()


def apply_matches(text: str, matches: list[Match]) -> str:
    out, cursor = [], 0
    for m in matches:
        out.append(text[cursor : m.start])
        out.append(m.replacement)
        cursor = m.end
    out.append(text[cursor:])
    return "".join(out)


def rewrite_pieces(pieces: list[str], matches: list[Match]) -> list[str]:
    """Apply matches found in ``"".join(pieces)`` while keeping the piece structure.

    Used for DOCX, where a single name is often split across several formatted runs: the
    replacement goes into the piece where the match starts and the matched text is removed
    from the following pieces, so formatting of the surrounding text is preserved.
    """
    full = "".join(pieces)
    result = []
    start = 0
    for piece in pieces:
        end = start + len(piece)
        out, cursor = [], start
        for m in matches:
            if m.end <= start or m.start >= end:
                continue
            if m.start > cursor:
                out.append(full[cursor : m.start])
            if m.start >= start:
                out.append(m.replacement)
            cursor = min(m.end, end)
        if cursor < end:
            out.append(full[cursor:end])
        result.append("".join(out))
        start = end
    return result
