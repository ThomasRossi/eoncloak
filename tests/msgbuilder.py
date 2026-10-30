"""Build synthetic Outlook .msg files for tests.

A .msg is an OLE Compound File Binary (MS-CFB) holding MAPI properties (MS-OXMSG). This is a
minimal CFB v3 writer (512-byte sectors, mini stream for small streams), enough for olefile
to read back. Test-only: no real messages are needed in the repository.
"""

from __future__ import annotations

import itertools
import struct
from dataclasses import dataclass, field

SECTOR, MINI_SECTOR, MINI_CUTOFF = 512, 64, 4096
FREE, END, FATSECT, NOSTREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF
STORAGE, STREAM, ROOT = 1, 2, 5


@dataclass
class _Node:
    name: str
    kind: int
    data: bytes = b""
    children: list[_Node] = field(default_factory=list)
    sid: int = 0
    start: int = END
    left: int = NOSTREAM
    right: int = NOSTREAM
    child: int = NOSTREAM

    def storage(self, name: str) -> _Node:
        for node in self.children:
            if node.name == name:
                return node
        node = _Node(name, STORAGE)
        self.children.append(node)
        return node

    def entry(self) -> bytes:
        name = self.name.encode("utf-16-le") + b"\0\0"
        size = len(self.data) if self.kind != STORAGE else 0
        start = self.start if self.kind != STORAGE else 0
        return struct.pack(
            "<64sHBBIII16sIQQIQ",
            name, len(name), self.kind, 1, self.left, self.right, self.child,
            b"\0" * 16, 0, 0, 0, start, size,
        )  # fmt: skip


def _sectors(n_bytes: int, size: int = SECTOR) -> int:
    return -(-n_bytes // size)


def build_cfb(streams: dict[str, bytes]) -> bytes:
    """``streams`` maps "storage/sub/stream" paths to their content."""
    root = _Node("Root Entry", ROOT)
    for path, data in streams.items():
        *storages, name = path.split("/")
        node = root
        for storage in storages:
            node = node.storage(storage)
        node.children.append(_Node(name, STREAM, data))

    entries: list[_Node] = []

    def number(node: _Node) -> None:
        node.sid = len(entries)
        entries.append(node)
        for child in node.children:
            number(child)

    number(root)
    for node in entries:
        kids = sorted(node.children, key=lambda n: (len(n.name), n.name.upper()))
        if kids:
            node.child = kids[0].sid
            for a, b in itertools.pairwise(kids):
                a.right = b.sid

    mini, minifat, big = bytearray(), [], []
    for node in entries:
        if node.kind != STREAM:
            continue
        if len(node.data) >= MINI_CUTOFF:
            big.append(node)
            continue
        n = _sectors(len(node.data), MINI_SECTOR)
        if n:
            node.start = len(mini) // MINI_SECTOR
            minifat += [node.start + i + 1 for i in range(n - 1)] + [END]
            mini += node.data.ljust(n * MINI_SECTOR, b"\0")

    dir_n = _sectors(len(entries) * 128)
    minifat_n = _sectors(len(minifat) * 4)
    mini_n = _sectors(len(mini))
    content_n = dir_n + minifat_n + mini_n + sum(_sectors(len(n.data)) for n in big)
    fat_n = 1
    while fat_n * (SECTOR // 4) < fat_n + content_n:
        fat_n += 1
    assert fat_n <= 109, "too large for this minimal writer"

    fat = [FATSECT] * fat_n

    def chain(n: int) -> int:
        if n == 0:
            return END
        start = len(fat)
        fat.extend([start + i + 1 for i in range(n - 1)] + [END])
        return start

    dir_start = chain(dir_n)
    minifat_start = chain(minifat_n)
    root.start = chain(mini_n)
    root.data = bytes(mini)  # the root entry's size is the mini stream size
    for node in big:
        node.start = chain(_sectors(len(node.data)))
    fat += [FREE] * (fat_n * (SECTOR // 4) - len(fat))

    unused = struct.pack("<64sHBBIII16sIQQIQ", b"", 0, 0, 0, NOSTREAM, NOSTREAM, NOSTREAM,
                         b"\0" * 16, 0, 0, 0, 0, 0)  # fmt: skip
    directory = b"".join(n.entry() for n in entries)
    directory += unused * (dir_n * SECTOR // 128 - len(entries))

    def pad(data: bytes, filler: bytes = b"\0") -> bytes:
        return data + filler * ((-len(data)) % SECTOR // len(filler))

    header = struct.pack(
        "<8s16sHHHHH6sIIIIIIIII",
        bytes.fromhex("D0CF11E0A1B11AE1"), b"\0" * 16, 0x3E, 3, 0xFFFE, 9, 6, b"\0" * 6,
        0, fat_n, dir_start, 0, MINI_CUTOFF, minifat_start, minifat_n, END, 0,
    ) + struct.pack("<109I", *(list(range(fat_n)) + [FREE] * (109 - fat_n)))  # fmt: skip

    body = struct.pack(f"<{len(fat)}I", *fat) + directory
    body += pad(struct.pack(f"<{len(minifat)}I", *minifat), struct.pack("<I", FREE))
    body += pad(bytes(mini))
    for node in big:
        body += pad(node.data)
    return header + body


def _unicode(prop_id: int, value: str) -> dict[str, bytes]:
    return {f"__substg1.0_{prop_id:04X}001F": value.encode("utf-16-le")}


def _fixed(header_size: int, props: dict[int, int]) -> bytes:
    data = b"\0" * header_size
    for tag, value in props.items():
        data += struct.pack("<IIQ", tag, 0x6, value)
    return data


def build_msg(
    *,
    subject: str,
    sender_name: str,
    sender_email: str,
    recipients: list[tuple[str, str, int]],
    body: str = "",
    html: str | None = None,
    attachments: list[str] = (),
    headers: str = "",
) -> bytes:
    """``recipients`` are (display name, SMTP address, type) with type 1=To, 2=Cc, 3=Bcc."""
    streams = {
        **_unicode(0x0037, subject),
        **_unicode(0x0C1A, sender_name),
        **_unicode(0x5D01, sender_email),
        "__properties_version1.0": _fixed(32, {0x3FDE0003: 65001}),
    }
    if body:
        streams.update(_unicode(0x1000, body))
    if html is not None:
        streams["__substg1.0_10130102"] = html.encode("utf-8")
    if headers:
        streams.update(_unicode(0x007D, headers))
    for i, (name, address, kind) in enumerate(recipients):
        storage = f"__recip_version1.0_#{i:08X}"
        for key, value in {**_unicode(0x3001, name), **_unicode(0x39FE, address)}.items():
            streams[f"{storage}/{key}"] = value
        streams[f"{storage}/__properties_version1.0"] = _fixed(8, {0x0C150003: kind})
    for i, filename in enumerate(attachments):
        for key, value in _unicode(0x3707, filename).items():
            streams[f"__attach_version1.0_#{i:08X}/{key}"] = value
    return build_cfb(streams)
