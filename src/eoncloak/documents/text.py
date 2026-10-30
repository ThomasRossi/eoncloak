from __future__ import annotations

from pathlib import Path

from ..replace import Replacer


class TextDocument:
    def __init__(self, content: str):
        self.content = content

    @classmethod
    def load(cls, path: Path) -> TextDocument:
        try:
            with path.open(encoding="utf-8-sig", newline="") as fh:
                return cls(fh.read())
        except UnicodeDecodeError as exc:
            raise ValueError(f"{path}: not a UTF-8 text file") from exc

    def text(self) -> str:
        return self.content

    def plain_text(self) -> str:
        return self.content

    def apply(self, replacer: Replacer, scrub_metadata: bool = True) -> None:
        self.content = replacer.sub(self.content)


def write_text(text: str, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
