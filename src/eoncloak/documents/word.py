"""DOCX support that rewrites text in place, keeping formatting.

Works on the WordprocessingML XML directly so that body, tables, text boxes, headers,
footers, footnotes, endnotes, comments, tracked changes, field codes and hyperlinks are all
covered, not only what python-docx exposes as paragraphs.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import docx
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.part import Part, XmlPart
from lxml import etree

from ..replace import Replacer, rewrite_pieces

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
EXTENDED_PROPS = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"

TEXT_PART_RE = re.compile(
    r"^/word/(document|header\d*|footer\d*|footnotes|endnotes|comments\w*|people)\.xml$"
)
APP_PROPS = "/docProps/app.xml"

# Attributes that only ever hold text (instructions of simple fields, image descriptions).
TEXT_ATTRS = {f"{W}instr", "descr", "title"}
# Attributes naming the people who wrote, commented or changed the document.
AUTHOR_ATTRS = {"author": "Author", "initials": "A", "userId": ""}
CORE_TEXT_PROPS = ("title", "subject", "keywords", "comments", "category")
CORE_PEOPLE_PROPS = ("author", "last_modified_by")


@dataclass
class _XmlPart:
    name: str
    part: Part
    root: etree._Element

    def commit(self) -> None:
        if not isinstance(self.part, XmlPart):
            self.part._blob = etree.tostring(
                self.root, xml_declaration=True, encoding="UTF-8", standalone=True
            )


def _nearest_paragraph(el: etree._Element) -> etree._Element | None:
    el = el.getparent()
    while el is not None and el.tag != f"{W}p":
        el = el.getparent()
    return el


class DocxDocument:
    def __init__(self, document: docx.document.Document):
        self.document = document
        self.parts: list[_XmlPart] = []
        for part in document.part.package.iter_parts():
            name = str(part.partname)
            if not (TEXT_PART_RE.match(name) or name == APP_PROPS):
                continue
            root = part.element if isinstance(part, XmlPart) else etree.fromstring(part.blob)
            self.parts.append(_XmlPart(name, part, root))

    @classmethod
    def load(cls, path: Path) -> DocxDocument:
        return cls(docx.Document(str(path)))

    # -- traversal -----------------------------------------------------------------------

    def _text_parts(self) -> Iterator[_XmlPart]:
        return (p for p in self.parts if p.name != APP_PROPS)

    def _paragraph_runs(self, root: etree._Element) -> Iterator[list[etree._Element]]:
        """Visible text nodes per paragraph (nested paragraphs, e.g. text boxes, separately)."""
        for paragraph in root.iter(f"{W}p"):
            nodes = [t for t in paragraph.iter(f"{W}t") if _nearest_paragraph(t) is paragraph]
            if nodes:
                yield nodes

    @staticmethod
    def _loose_nodes(root: etree._Element) -> Iterator[etree._Element]:
        """Deleted text (tracked changes) and field instructions, rewritten node by node."""
        yield from root.iter(f"{W}delText", f"{W}instrText")

    def _hyperlinks(self):
        for part in self.document.part.package.iter_parts():
            for rel in part.rels.values():
                if rel.is_external and rel.reltype == RT.HYPERLINK:
                    yield rel

    # -- Document protocol ---------------------------------------------------------------

    def text(self) -> str:
        lines: list[str] = []
        props = self.document.core_properties
        lines += [getattr(props, name) or "" for name in CORE_TEXT_PROPS + CORE_PEOPLE_PROPS]
        for xml in self._text_parts():
            lines += [
                "".join(t.text or "" for t in nodes) for nodes in self._paragraph_runs(xml.root)
            ]
            lines += [node.text or "" for node in self._loose_nodes(xml.root)]
            lines += [
                value
                for el in xml.root.iter()
                for key, value in el.attrib.items()
                if key in TEXT_ATTRS
            ]
        lines += [rel.target_ref for rel in self._hyperlinks()]
        return "\n".join(line for line in lines if line)

    def plain_text(self) -> str:
        body = next(p for p in self.parts if p.name == "/word/document.xml")
        return "\n".join(
            "".join(t.text or "" for t in nodes) for nodes in self._paragraph_runs(body.root)
        )

    def apply(self, replacer: Replacer, scrub_metadata: bool = True) -> None:
        for xml in self._text_parts():
            for nodes in self._paragraph_runs(xml.root):
                pieces = [t.text or "" for t in nodes]
                matches = replacer.find("".join(pieces))
                if not matches:
                    continue
                for node, new_text in zip(nodes, rewrite_pieces(pieces, matches), strict=True):
                    if new_text != node.text:
                        node.text = new_text
                        node.set(XML_SPACE, "preserve")
            for node in self._loose_nodes(xml.root):
                if node.text:
                    node.text = replacer.sub(node.text)
            for el in xml.root.iter():
                for key, value in el.attrib.items():
                    if key in TEXT_ATTRS:
                        el.set(key, replacer.sub(value))
        for rel in self._hyperlinks():
            rel._target = replacer.sub(rel.target_ref)

        props = self.document.core_properties
        for name in CORE_TEXT_PROPS + CORE_PEOPLE_PROPS:
            value = getattr(props, name)
            if value:
                setattr(props, name, replacer.sub(value))
        if scrub_metadata:
            self._scrub_metadata()

    def _scrub_metadata(self) -> None:
        props = self.document.core_properties
        for name in CORE_PEOPLE_PROPS:
            setattr(props, name, "")
        for xml in self.parts:
            if xml.name == APP_PROPS:
                for tag in ("Company", "Manager"):
                    for el in xml.root.iter(f"{EXTENDED_PROPS}{tag}"):
                        el.text = ""
                continue
            for el in xml.root.iter():
                for key in list(el.attrib):
                    replacement = AUTHOR_ATTRS.get(etree.QName(key).localname)
                    if replacement is not None:
                        el.set(key, replacement)

    def save(self, path: Path) -> None:
        for xml in self.parts:
            xml.commit()
        self.document.save(str(path))


def write_docx_from_text(text: str, path: Path) -> None:
    document = docx.Document()
    props = document.core_properties
    for name in (*CORE_TEXT_PROPS, *CORE_PEOPLE_PROPS):
        setattr(props, name, "")
    for line in text.split("\n"):
        document.add_paragraph(line)
    document.save(str(path))
