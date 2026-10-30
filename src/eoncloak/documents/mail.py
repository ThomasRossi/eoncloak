"""Email messages: ``.eml`` (RFC 822/MIME) and Outlook ``.msg``.

A message becomes plain text: the identifying headers (From, To, Cc, Bcc, Reply-To, Date,
Subject), the attachment names and the body. Routing headers (Received, Message-ID, X-*)
are dropped. Attachment contents are not processed.

``.msg`` files are OLE compound files holding MAPI properties; they are read with olefile
(BSD) rather than extract-msg, which is GPL.
"""

from __future__ import annotations

import codecs
import contextlib
import logging
import re
import struct
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser, HeaderParser
from html.parser import HTMLParser
from pathlib import Path

import olefile

from .text import TextDocument

log = logging.getLogger(__name__)

HEADERS = ("From", "To", "Cc", "Bcc", "Reply-To", "Date", "Subject")


class EmailDocument(TextDocument):
    """An email flattened to text; output is plain text, Markdown or DOCX."""

    @classmethod
    def load(cls, path: Path) -> EmailDocument:
        if path.suffix.lower() == ".msg":
            headers, attachments, body = _read_msg(path)
        else:
            headers, attachments, body = _read_eml(path)
        return cls(format_email(headers, attachments, body))


def format_email(headers: list[tuple[str, str]], attachments: list[str], body: str) -> str:
    lines = [f"{name}: {value}" for name, value in headers if value]
    if attachments:
        lines.append("Attachments: " + ", ".join(attachments))
    return "\n".join(lines) + "\n\n" + body.strip() + "\n"


# -- .eml --------------------------------------------------------------------------------


def _read_eml(path: Path) -> tuple[list[tuple[str, str]], list[str], str]:
    with path.open("rb") as fh:
        message: EmailMessage = BytesParser(policy=policy.default).parse(fh)
    headers = [(name, str(message.get(name, ""))) for name in HEADERS]
    attachments = [part.get_filename() or "(unnamed)" for part in message.iter_attachments()]
    body_part = message.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        body = body_part.get_content()
        if body_part.get_content_subtype() == "html":
            body = html_to_text(body)
    return headers, attachments, body


# -- .msg --------------------------------------------------------------------------------

PR_SUBJECT = "0037"
PR_TRANSPORT_HEADERS = "007D"
PR_SENT_REPRESENTING_NAME = "0042"
PR_SENDER_NAME = "0C1A"
PR_SENDER_EMAIL = "0C1F"
PR_SENDER_SMTP = "5D01"
PR_BODY = "1000"
PR_RTF_COMPRESSED = "1009"
PR_HTML = "1013"
PR_DISPLAY_NAME = "3001"
PR_EMAIL_ADDRESS = "3003"
PR_SMTP_ADDRESS = "39FE"
PR_ATTACH_FILENAME = "3704"
PR_ATTACH_LONG_FILENAME = "3707"
PR_RECIPIENT_TYPE = 0x0C150003
PR_INTERNET_CPID = 0x3FDE0003
PR_MESSAGE_CODEPAGE = 0x3FFD0003

RECIPIENT_TYPES = {1: "To", 2: "Cc", 3: "Bcc"}


class _Msg:
    def __init__(self, ole: olefile.OleFileIO):
        self.ole = ole
        fixed = self.fixed_properties("", header_size=32)
        cpid = fixed.get(PR_INTERNET_CPID) or fixed.get(PR_MESSAGE_CODEPAGE)
        self.encoding = "cp1252"
        if cpid:
            with contextlib.suppress(LookupError):
                self.encoding = codecs.lookup(f"cp{cpid}").name

    def raw(self, storage: str, prop_id: str, prop_type: str) -> bytes | None:
        path = f"{storage}__substg1.0_{prop_id}{prop_type}"
        return self.ole.openstream(path).read() if self.ole.exists(path) else None

    def string(self, storage: str, prop_id: str) -> str:
        data = self.raw(storage, prop_id, "001F")
        if data is not None:
            return data.decode("utf-16-le", errors="replace").rstrip("\0")
        data = self.raw(storage, prop_id, "001E")
        if data is not None:
            return data.decode(self.encoding, errors="replace").rstrip("\0")
        return ""

    def fixed_properties(self, storage: str, header_size: int) -> dict[int, int]:
        """Integer properties from the ``__properties_version1.0`` stream."""
        path = f"{storage}__properties_version1.0"
        if not self.ole.exists(path):
            return {}
        data = self.ole.openstream(path).read()
        values = {}
        for offset in range(header_size, len(data) - 15, 16):
            tag, _flags, value = struct.unpack_from("<IIQ", data, offset)
            values[tag] = value & 0xFFFFFFFF
        return values

    def children(self, prefix: str) -> list[str]:
        names = {entry[0] for entry in self.ole.listdir(streams=False, storages=True)}
        return sorted(f"{name}/" for name in names if name.startswith(prefix))


def _address(name: str, address: str) -> str:
    if address and "@" not in address:
        address = ""  # Exchange X.500 addresses carry no SMTP address
    if name and address and name != address:
        return f"{name} <{address}>"
    return name or address


def _read_msg(path: Path) -> tuple[list[tuple[str, str]], list[str], str]:
    if not olefile.isOleFile(str(path)):
        raise ValueError(f"{path}: not an Outlook .msg file")
    with olefile.OleFileIO(str(path)) as ole:
        msg = _Msg(ole)
        transport = HeaderParser(policy=policy.default).parsestr(
            msg.string("", PR_TRANSPORT_HEADERS)
        )

        sender = _address(
            msg.string("", PR_SENDER_NAME) or msg.string("", PR_SENT_REPRESENTING_NAME),
            msg.string("", PR_SENDER_SMTP) or msg.string("", PR_SENDER_EMAIL),
        )
        recipients: dict[str, list[str]] = {"To": [], "Cc": [], "Bcc": []}
        for storage in msg.children("__recip_version1.0_#"):
            kind = RECIPIENT_TYPES.get(
                msg.fixed_properties(storage, header_size=8).get(PR_RECIPIENT_TYPE, 1), "To"
            )
            recipients[kind].append(
                _address(
                    msg.string(storage, PR_DISPLAY_NAME),
                    msg.string(storage, PR_SMTP_ADDRESS) or msg.string(storage, PR_EMAIL_ADDRESS),
                )
            )
        headers = [
            ("From", sender or str(transport.get("From", ""))),
            *((kind, "; ".join(r for r in names if r)) for kind, names in recipients.items()),
            ("Reply-To", str(transport.get("Reply-To", ""))),
            ("Date", str(transport.get("Date", ""))),
            ("Subject", msg.string("", PR_SUBJECT)),
        ]
        attachments = [
            msg.string(storage, PR_ATTACH_LONG_FILENAME)
            or msg.string(storage, PR_ATTACH_FILENAME)
            or msg.string(storage, PR_DISPLAY_NAME)
            or "(unnamed)"
            for storage in msg.children("__attach_version1.0_#")
        ]

        body = msg.string("", PR_BODY)
        if not body.strip():
            html = msg.raw("", PR_HTML, "0102")
            if html is not None:
                body = html_to_text(html.decode(msg.encoding, errors="replace"))
            else:
                body = html_to_text(msg.string("", PR_HTML))
        if not body.strip() and msg.raw("", PR_RTF_COMPRESSED, "0102") is not None:
            log.warning(
                "%s: the message body is only stored as RTF, which is not supported yet", path
            )
    return headers, attachments, body


# -- HTML --------------------------------------------------------------------------------

_BLOCK_TAGS = {
    "address", "blockquote", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li",
    "p", "pre", "table", "tr",
}  # fmt: skip


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "head", "title"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("\t")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head", "title"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(re.sub(r"\s+", " ", data))


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines = [line.strip() for line in "".join(parser.parts).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
