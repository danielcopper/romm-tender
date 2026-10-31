"""Whether a PS3 image still lies encrypted, read the way RPCS3 reads it — stdlib only.

RPCS3 tells an encrypted image from a decrypted one by two files inside it
(``read_magic_blocks``, ``rpcs3/Loader/ISO.cpp:228-279`` at d5213de6, the
build RetroDECK ships and the RPCS3 save card pins, held by
``tests/test_standalone_build_tripwire.py`` against RetroDECK's deployed binary;
EmuDeck's AppImage build is not pinned): ``PS3_GAME/LICDIR/LIC.DAT`` and
``PS3_GAME/USRDIR/EBOOT.BIN``, in that order, each looked up in the image's
ISO 9660 tree and its first 16 bytes read. A file the image does not hold is
skipped; a block that comes back short ends the read with no verdict
(``NOT_ISO``, :270-273), and so does an image holding neither file
(:278). Where blocks were read, the image is encrypted when any of them does
not open with its magic in the clear — ``PS3LICDA`` for the licence, ``SCE``
for the executable (``is_any_block_encrypted``, :283-289). With no verdict
RPCS3 finds nothing encrypted (``get_key_status``, :686-708).

The tree is RPCS3's own reading of the image (``iso_archive``, :1216-1311):
the volume descriptors from ``0x8000`` on, one sector each, until the
terminator (type 255) — the root directory record of the last primary (1) or
supplementary (2) descriptor is the root, a supplementary one's names in
UCS-2 big endian (:1239-1293). A directory record is read by
``iso_read_directory_entry`` (:971-1106): its length byte, zero marking the
rest of the sector as padding; a 32-byte header whose sector and size are the
little-endian halves of their both-endian fields; the name, with a trailing
``;1`` and then a trailing ``.`` removed (:1053-1085). A path is looked up component by
component, the first child of that name in record order
(``iso_archive::retrieve``, :1313-1387).

The walk reads only the directories on the two paths, so it is bounded by
what the image names: at most :data:`DESCRIPTOR_LIMIT` descriptors and
:data:`DIRECTORY_LIMIT` bytes of any one directory. An image past either
bound, or one whose records RPCS3 would refuse, yields no verdict here
(``None``) — RPCS3 refuses such an image's whole tree, and this reading does
not follow it there. Nor does it check the rest of the tree, which RPCS3
reads in full before it boots anything (``iso_form_hierarchy``, :1108-1202).
"""

from __future__ import annotations

from typing import BinaryIO, Final, Literal

SECTOR: Final = 2048
_DESCRIPTORS = SECTOR * 16
_TERMINATOR = 255
_PRIMARY = 1
_SUPPLEMENTARY = 2
# The root directory record's place in a volume descriptor: past the type byte and 155 more (ISO.cpp:1263-1267).
_ROOT_RECORD = 156
_RECORD_HEADER = 33
_DIRECTORY_FLAG = 0b00000010
# The two files and the magic each opens with in the clear (ISO.cpp:237-240).
_MAGIC_FILES = (
    (("PS3_GAME", "LICDIR", "LIC.DAT"), b"PS3LICDA"),
    (("PS3_GAME", "USRDIR", "EBOOT.BIN"), b"SCE"),
)
_BLOCK = 16
# How far this reading follows an image, which comes with downloaded content.
# A PS3 image carries a primary descriptor, a Joliet one and the terminator,
# and a directory of a few hundred files fits in a few sectors; the bounds
# leave room for far more and keep a crafted image from costing more than a
# few MiB of reads.
DESCRIPTOR_LIMIT: Final = 32
DIRECTORY_LIMIT: Final = 1024 * 1024

Blocks = Literal["clear", "encrypted", "none"]
BLOCKS_CLEAR: Blocks = "clear"
BLOCKS_ENCRYPTED: Blocks = "encrypted"
BLOCKS_NONE: Blocks = "none"
BLOCKS: tuple[Blocks, ...] = (BLOCKS_CLEAR, BLOCKS_ENCRYPTED, BLOCKS_NONE)


class _Refused(Exception):
    """A record or a bound this reading does not follow past."""


class _Record:
    """One directory record: its name as RPCS3 normalises it, its first extent, and whether it is a directory."""

    __slots__ = ("name", "start", "size", "directory")

    def __init__(self, name: str, start: int, size: int, directory: bool) -> None:
        self.name = name
        self.start = start
        self.size = size
        self.directory = directory


def magic_blocks(handle: BinaryIO, size: int) -> Blocks | None:
    """What RPCS3's two magic blocks say of the image *handle* reads — ``None`` where this reading cannot tell."""
    try:
        root, ucs2 = _root(handle, size)
        blocks: list[tuple[bytes, bytes]] = []
        for path, magic in _MAGIC_FILES:
            record = _lookup(handle, size, root, path, ucs2)
            if record is None:
                continue
            block = _read(handle, record.start * SECTOR, min(_BLOCK, record.size), size)
            if len(block) != _BLOCK:
                return BLOCKS_NONE
            blocks.append((block, magic))
    except _Refused:
        return None
    if not blocks:
        return BLOCKS_NONE
    return BLOCKS_ENCRYPTED if any(not block.startswith(magic) for block, magic in blocks) else BLOCKS_CLEAR


def _read(handle: BinaryIO, offset: int, length: int, size: int) -> bytes:
    if offset >= size:
        return b""
    handle.seek(offset)
    return handle.read(min(length, size - offset))


def _root(handle: BinaryIO, size: int) -> tuple[_Record, bool]:
    """The root of the last primary or supplementary descriptor before the terminator, and whether names are UCS-2."""
    root: tuple[_Record, bool] | None = None
    for index in range(DESCRIPTOR_LIMIT):
        offset = _DESCRIPTORS + index * SECTOR
        sector = _read(handle, offset, SECTOR, size)
        if not sector:
            raise _Refused("the descriptors run out before the terminator")
        if sector[0] == _TERMINATOR:
            return _checked_root(root)
        root = _descriptor_root(sector, root)
        if offset + SECTOR >= size:
            raise _Refused("the descriptors run out before the terminator")
    raise _Refused("more descriptors than this reading follows")


def _descriptor_root(sector: bytes, root: tuple[_Record, bool] | None) -> tuple[_Record, bool] | None:
    """The root a primary or supplementary descriptor names, replacing *root*; any other descriptor keeps it."""
    if sector[0] not in (_PRIMARY, _SUPPLEMENTARY):
        return root
    ucs2 = sector[0] == _SUPPLEMENTARY
    record = _record(sector, _ROOT_RECORD, ucs2)
    return root if record is None else (record, ucs2)


def _checked_root(root: tuple[_Record, bool] | None) -> tuple[_Record, bool]:
    """A root with no name leaves the archive invalid (``is_valid``, ISO.cpp:1395-1398), and one that is no
    directory fails the hierarchy it heads (:1108-1113)."""
    if root is None or not root[0].name or not root[0].directory:
        raise _Refused("no descriptor names a root directory")
    return root


def _record(data: bytes, offset: int, ucs2: bool) -> _Record | None:
    """``iso_read_directory_entry`` at *offset* — ``None`` for padding, :class:`_Refused` for a record RPCS3 refuses."""
    if offset >= len(data):
        raise _Refused("a record past its sector")
    length = data[offset]
    if length == 0:
        return None
    name_length = data[offset + 32] if offset + 32 < len(data) else 0
    if length < _RECORD_HEADER or offset + length > len(data) or name_length > length - _RECORD_HEADER:
        raise _Refused("a directory record RPCS3 refuses")
    start = int.from_bytes(data[offset + 2 : offset + 6], "little")
    extent = int.from_bytes(data[offset + 10 : offset + 14], "little")
    raw = data[offset + _RECORD_HEADER : offset + _RECORD_HEADER + name_length]
    return _Record(_name(raw, ucs2), start, extent, bool(data[offset + 25] & _DIRECTORY_FLAG))


def _name(raw: bytes, ucs2: bool) -> str:
    """A record's name as RPCS3 keeps it (ISO.cpp:1053-1085)."""
    if raw == b"\x00":
        return "."
    if raw == b"\x01":
        return ".."
    name = raw[: len(raw) // 2 * 2].decode("utf-16-be", "replace") if ucs2 else raw.decode("latin-1")
    name = name.removesuffix(";1")
    if len(name) > 1 and name.endswith("."):
        name = name[:-1]
    return name


def _lookup(handle: BinaryIO, size: int, root: _Record, path: tuple[str, ...], ucs2: bool) -> _Record | None:
    """``iso_archive::retrieve`` over *path*: each component the first child of that name, ``None`` where absent."""
    node = root
    for component in path:
        if not node.directory:
            return None
        found = next((child for child in _children(handle, size, node, ucs2) if child.name == component), None)
        if found is None:
            return None
        node = found
    return node


def _children(handle: BinaryIO, size: int, directory: _Record, ucs2: bool) -> list[_Record]:
    """The records of *directory*'s extent in order, padding skipped to the next sector (ISO.cpp:1127-1155)."""
    start = directory.start * SECTOR
    if start > size or directory.size > size - start:
        raise _Refused("a directory past the end of the image")
    if directory.size > DIRECTORY_LIMIT:
        raise _Refused("a directory larger than this reading follows")
    data = _read(handle, start, directory.size, size)
    children: list[_Record] = []
    position = 0
    while position < len(data):
        record = _record(data, position, ucs2)
        if record is None:
            # The sector after the one the length byte was read from, past it (ISO.cpp:1149-1154).
            position = ((position + 1) // SECTOR + 1) * SECTOR
            continue
        position += data[position]
        children.append(record)
    return children
