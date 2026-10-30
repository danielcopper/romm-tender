"""Read what RPCS3's boot asks of one file's bytes — stdlib only.

RPCS3 decides how a path boots from a handful of reads of the file's own
bytes (RPCS3 d5213de6, the build RetroDECK ships and the RPCS3 save card
pins, held by ``tests/test_standalone_build_tripwire.py`` against RetroDECK's
deployed binary; EmuDeck's AppImage build is not pinned). This module
performs those reads and nothing past them:

- **the leading four bytes**, which three tests compare: ``Emulator::IsValidSfb``
  takes a ``PS3_DISC.SFB`` whose first ``u32`` reads ``.SFB``
  (``rpcs3/Emu/System.cpp:5052-5067``), ``Emulator::Load`` decrypts a file
  opening with ``SCE\\0`` as a SELF (:2543-2559), and a file opening with
  ``\\x7fELF`` is an ELF its executable loaders open (:2561-2815);
- **the ISO 9660 identifier**: ``is_iso_file`` takes a file holding at least
  ``0x8006`` bytes whose ``CD001`` follows the first volume descriptor's type
  byte at ``0x8000`` (``rpcs3/Loader/ISO.cpp:63-118``), and only then is the
  image's header read — the big-endian region count at offset 0, which a PS3
  image holds between 1 and 127 of (``iso_file_decryption::init``,
  :514-551), and the 3k3y watermark at ``0xF70`` (:606-650) — and, for an
  image declaring encrypted regions, the two blocks inside it RPCS3 tells
  an encrypted image by (:mod:`atlas.ps3_iso`);
- **the PARAM.SFO's category**, read the way ``psf::load`` reads it
  (``rpcs3/Loader/PSF.cpp:182-284``): a 20-byte little-endian header with the
  magic ``\\0PSF`` and version ``0x101``, a table of 16-byte entries, a key
  table and a data table, each step checked as the loader checks it. Any
  failed check, and a ``CATEGORY`` outside the loader's list of known ones
  (:273-280), clears the whole registry — so the category is then the empty
  string, which is exactly what ``Emulator::Load`` reads from it (:1742-1746).

Nothing here decides a boot; :mod:`atlas.rpcs3_boot` does, over these facts.
Every read is bounded by the file's own size before it is made, so a header
naming a table larger than the file is a short read — the loader's
``corrupt`` — rather than an allocation, and a PARAM.SFO larger than
:data:`PSF_SIZE_LIMIT` is not parsed at all (:class:`Ps3FileUnread`).
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import BinaryIO, Final, Literal

from .ps3_iso import BLOCKS, Blocks, magic_blocks

MAGIC_LENGTH: Final = 4
MAGIC_SFB: Final = ".SFB"
MAGIC_SELF: Final = "SCE\x00"
MAGIC_ELF: Final = "\x7fELF"
MAGIC_PSF: Final = "\x00PSF"

# ``ISO_SECTOR_SIZE`` and ``ISO_DESCRIPTORS_OFFSET`` (rpcs3/Loader/ISO.h:28-31).
ISO_SECTOR_SIZE: Final = 2048
ISO_DESCRIPTORS_OFFSET: Final = ISO_SECTOR_SIZE * 16
_ISO_IDENTIFIER = b"CD001"
_ISO_MINIMUM_SIZE = ISO_DESCRIPTORS_OFFSET + 6
# The region count ``init`` accepts (ISO.cpp:539-543).
ISO_REGIONS_MINIMUM: Final = 1
ISO_REGIONS_MAXIMUM: Final = 127
_WATERMARK_OFFSET = 0xF70
_WATERMARK_ENCRYPTED = b"Encrypted 3K BLD"
_WATERMARK_DECRYPTED = b"Dncrypted 3K BLD"

Watermark = Literal["encrypted", "decrypted"]
WATERMARK_ENCRYPTED: Watermark = "encrypted"
WATERMARK_DECRYPTED: Watermark = "decrypted"
WATERMARKS: tuple[Watermark, ...] = (WATERMARK_ENCRYPTED, WATERMARK_DECRYPTED)

# ``valid_cats`` (PSF.cpp:274): a CATEGORY outside it clears the registry.
PSF_CATEGORIES: Final = (
    "GD", "DG", "HG", "AM", "AP", "AS", "AT", "AV", "BV", "WT", "HM",
    "CB", "SF", "2P", "2G", "1P", "PP", "MN", "PE", "2D", "SD", "MS",
)  # fmt: skip
_PSF_HEADER = struct.Struct("<IIIII")
_PSF_ENTRY = struct.Struct("<HHIII")
_PSF_MAGIC = int.from_bytes(MAGIC_PSF.encode("latin-1"), "little")
_PSF_VERSION = 0x101
# ``psf::format`` (rpcs3/Loader/PSF.h:35-40).
_PSF_ARRAY = 0x0004
_PSF_STRING = 0x0204
_PSF_INTEGER = 0x0404
_PSF_CATEGORY = b"CATEGORY"
# The largest PARAM.SFO atlas parses. RPCS3's loader sets no bound of its own
# (PSF.cpp:182-284 reads whatever the header names, up to the file's size),
# while a PS3 PARAM.SFO holds a few dozen short entries and fits in a few
# KiB. Each entry's key is read up to its NUL, so a crafted key table without
# one costs up to the table's length per entry — quadratic in the file's size
# — and the bound is what keeps that cheap. A larger file is not read, and the
# read answers that rather than an empty registry, which would claim a boot
# RPCS3 might not make: it reads the file whatever its size.
PSF_SIZE_LIMIT: Final = 64 * 1024


class Ps3FileUnread(Exception):
    """A file atlas does not read as RPCS3 would — a PARAM.SFO larger than :data:`PSF_SIZE_LIMIT`."""


@dataclass(frozen=True, slots=True)
class Ps3IsoHeader:
    """The facts of a PS3 image that decide whether anything in it is encrypted: its header, and its magic blocks."""

    regions: int
    """The region count the first sector states, big endian — between 1 and 127 for an image RPCS3 opens, and 1
    for one with nothing encrypted, because only the odd regions are."""
    watermark: Watermark | None
    """The 3k3y watermark at ``0xF70``: ``encrypted`` for an image carrying its own key, ``decrypted`` for one
    already decrypted, ``None`` where neither is written."""
    blocks: Blocks | None = None
    """What RPCS3's licence and executable blocks say (:func:`atlas.ps3_iso.magic_blocks`): ``clear``,
    ``encrypted``, or ``none`` where it read no block to judge — read only for an image declaring encrypted regions,
    and ``None`` for any other, or where the image's tree is one this reading does not follow."""

    def __post_init__(self) -> None:
        if self.watermark is not None and self.watermark not in WATERMARKS:
            raise ValueError(f"Ps3IsoHeader: watermark must be one of {WATERMARKS} or None, got {self.watermark!r}")
        if self.blocks is not None and self.blocks not in BLOCKS:
            raise ValueError(f"Ps3IsoHeader: blocks must be one of {BLOCKS} or None, got {self.blocks!r}")


@dataclass(frozen=True, slots=True)
class Ps3FileFacts:
    """What RPCS3's boot reads of one file: its leading bytes, its ISO header, its PARAM.SFO category."""

    magic: str
    """The file's first four bytes, one character per byte (latin-1) — fewer where the file is shorter."""
    iso: Ps3IsoHeader | None = None
    """The image header where ``is_iso_file`` takes the file, ``None`` where it does not."""
    category: str = ""
    """What ``psf::load`` yields for ``CATEGORY`` — empty where the file is no PARAM.SFO the loader keeps."""

    def __post_init__(self) -> None:
        if len(self.magic) > MAGIC_LENGTH:
            raise ValueError(f"Ps3FileFacts: magic is at most {MAGIC_LENGTH} bytes, got {self.magic!r}")
        if self.category and (self.magic != MAGIC_PSF or self.category not in PSF_CATEGORIES):
            raise ValueError(
                "Ps3FileFacts: a category is read only out of a PARAM.SFO the loader keeps — the magic "
                f"{MAGIC_PSF!r} and one of {PSF_CATEGORIES}"
            )

    @property
    def executable(self) -> bool:
        """Whether RPCS3's executable loaders take the file: a SELF it decrypts, or an ELF."""
        return self.magic in (MAGIC_SELF, MAGIC_ELF)


def read_facts(handle: BinaryIO) -> Ps3FileFacts:
    """The facts of the file *handle* reads, from its start — every read bounded by the file's size."""
    size = os.fstat(handle.fileno()).st_size
    handle.seek(0)
    magic = handle.read(MAGIC_LENGTH).decode("latin-1")
    return Ps3FileFacts(
        magic=magic,
        iso=_iso_header(handle, size),
        category=_psf_category(handle, size) if magic == MAGIC_PSF else "",
    )


def _read_at(handle: BinaryIO, offset: int, length: int) -> bytes:
    handle.seek(offset)
    return handle.read(length)


def _iso_header(handle: BinaryIO, size: int) -> Ps3IsoHeader | None:
    """``is_iso_file``, then the region count and the watermark — ``None`` where the file is no ISO."""
    if size < _ISO_MINIMUM_SIZE:
        return None
    if _read_at(handle, ISO_DESCRIPTORS_OFFSET + 1, len(_ISO_IDENTIFIER)) != _ISO_IDENTIFIER:
        return None
    regions = int.from_bytes(_read_at(handle, 0, 4), "big")
    stamp = _read_at(handle, _WATERMARK_OFFSET, len(_WATERMARK_ENCRYPTED))
    watermark: Watermark | None = None
    if stamp == _WATERMARK_ENCRYPTED:
        watermark = WATERMARK_ENCRYPTED
    elif stamp == _WATERMARK_DECRYPTED:
        watermark = WATERMARK_DECRYPTED
    encrypted_regions = ISO_REGIONS_MINIMUM < regions <= ISO_REGIONS_MAXIMUM
    return Ps3IsoHeader(regions, watermark, magic_blocks(handle, size) if encrypted_regions else None)


def _psf_category(handle: BinaryIO, size: int) -> str:
    """``psf::load``'s ``CATEGORY``, empty wherever one of its checks clears the registry (PSF.cpp:196-284).

    The whole file is read once, and only up to :data:`PSF_SIZE_LIMIT`: a
    larger one raises :class:`Ps3FileUnread` instead of being parsed.
    """
    if size > PSF_SIZE_LIMIT:
        raise Ps3FileUnread(f"a PARAM.SFO of {size} bytes is beyond the {PSF_SIZE_LIMIT} atlas reads")
    entries = _psf_entries(_read_at(handle, 0, size))
    if entries is None:
        return ""
    category = ""
    for key, value in entries:
        if key == _PSF_CATEGORY and value is not None:
            category = value.decode("latin-1")
    return category if category in PSF_CATEGORIES else ""


def _psf_layout(data: bytes) -> tuple[int, int, int] | None:
    """The header's key-table offset, data-table offset and entry count — ``None`` where the loader refuses the
    header or the entry table runs past the file (PSF.cpp:198-213)."""
    if len(data) < _PSF_HEADER.size:
        return None
    magic, version, off_keys, off_data, count = _PSF_HEADER.unpack_from(data)
    if magic != _PSF_MAGIC or version != _PSF_VERSION:
        return None
    if off_keys < _PSF_HEADER.size or off_keys > off_data or off_data > len(data):
        return None
    if _PSF_HEADER.size + count * _PSF_ENTRY.size > len(data):
        return None
    return off_keys, off_data, count


def _psf_entries(data: bytes) -> list[tuple[bytes, bytes | None]] | None:
    """Each kept entry's key and string value (``None`` for an integer), or ``None`` for a file the loader refuses.

    An entry in a format the loader does not know is skipped without being
    kept (PSF.cpp:266-270), so it never counts as a key seen twice. A key
    runs from its offset to the next NUL, or to the end of the key table
    (``std::string(keys.data() + key_off)``, :226, over a table the string
    ends with a NUL past); its offset is checked before the key is read.
    """
    layout = _psf_layout(data)
    if layout is None:
        return None
    off_keys, off_data, count = layout
    entries: dict[bytes, bytes | None] = {}
    for index in range(count):
        key_off, fmt, length, maximum, data_off = _PSF_ENTRY.unpack_from(
            data, _PSF_HEADER.size + index * _PSF_ENTRY.size
        )
        if key_off >= off_data - off_keys:
            return None
        key = _psf_key(data, off_keys + key_off, off_data)
        if key in entries or not _psf_entry_fits(length, maximum, data_off, off_data, len(data)):
            return None
        outcome = _psf_value(data, off_data + data_off, fmt, length, maximum)
        if outcome is _SHORT:
            return None
        if outcome is not _SKIPPED:
            entries[key] = outcome if isinstance(outcome, bytes) else None
    return list(entries.items())


def _psf_key(data: bytes, start: int, end: int) -> bytes:
    """The key from *start* to the next NUL, or to *end*, the key table's end — found first, then sliced once."""
    nul = data.find(b"\x00", start, end)
    return data[start : end if nul == -1 else nul]


def _psf_entry_fits(length: int, maximum: int, data_off: int, off_data: int, size: int) -> bool:
    """The loader's three checks of one entry's sizes against the file (PSF.cpp:230-232)."""
    return length <= maximum and data_off < size - off_data and maximum < size - data_off


class _Outcome:
    """A value the loader does not keep as a string: a short read, a skipped entry, an integer."""


_SHORT = _Outcome()
_SKIPPED = _Outcome()
_INTEGER = _Outcome()


def _psf_value(data: bytes, offset: int, fmt: int, length: int, maximum: int) -> bytes | _Outcome:
    """One entry's value as the loader reads it (PSF.cpp:236-270): the string, or what became of it instead —
    a short read clears the registry, an unknown format is skipped, an integer is kept as no string."""
    if fmt == _PSF_INTEGER and maximum == 4 and length == 4:
        return _INTEGER if offset + 4 <= len(data) else _SHORT
    if fmt not in (_PSF_STRING, _PSF_ARRAY):
        return _SKIPPED
    if offset + length > len(data):
        return _SHORT
    value = data[offset : offset + length]
    return value.split(b"\x00", 1)[0] if fmt == _PSF_STRING else value
