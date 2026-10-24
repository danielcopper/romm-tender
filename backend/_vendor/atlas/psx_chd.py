"""Read a PlayStation disc stored as a CHD the way libchdr reads it — stdlib only, its codecs guarded.

A CHD ("Compressed Hunks of Data", MAME's container) holds a CD as hunks of
whole frames — 2352 sector bytes and 96 subcode bytes each — compressed one
hunk at a time, found through a map, and described by track metadata. The three
emulators that decide a PlayStation disc's region from its bytes all open a CHD
through a vendored libchdr: DuckStation's ``dep/libchdr`` at
RetroDECK/Duckstation@1f6de046b (the citations below without a repository name
are to that copy), SwanStation's at libretro/swanstation@4d309c0 and Beetle
PSX's ``deps/libchdr`` at libretro/beetle-psx-libretro@d6383bf. The three copies
read a hunk the same way and differ in which codecs they carry and in what the
emulator around them does with a CHD that refers to a parent.

This module reads version 5 only — the version chdman has written since 2012 —
and of it the hunks the identity needs, never the whole image:

- the header (``header_read``, ``libchdr_chd.c:2518-2657``; ``header_validate``
  :2428-2485, which checks nothing of a version 5 header past its version and
  length);
- the map, Huffman-coded as ``decompress_v5_map`` decodes it (:1537-1702), its
  CRC checked as libchdr checks it — every entry is decoded, because each
  compressed hunk's offset is the running sum of the lengths before it;
- the track metadata (``CHT2``, or ``CHTR`` before it, :2345-2393 and
  :3067-3106), parsed and checked the way DuckStation's CD reader does
  (``src/util/cd_image_chd.cpp:237-421``);
- a hunk (``hunk_read_into_memory``, :2750-2972): compressed with ``cdlz``,
  ``cdzl`` or ``cdzs`` (:689-732, :772-815, :1132-1175), stored uncompressed, a
  copy of another hunk of the same file, or a hunk's worth of units in the
  parent — every one checked against the CRC its map entry carries, after the
  sync and parity a CD codec strips are rebuilt (``ecc_generate``,
  ``libchdr_cdrom.c:416-426``), because the CRC is of the frame as it was.

A parent is found the way DuckStation finds it (D18 of #661,
``cd_image_chd.cpp:106-235``): among the files of the child's own folder, the
first in directory order whose header matches the child's parent hash and which
itself opens, through further parents, to DuckStation's own depth limit
(:79, :124-129). SwanStation and Beetle PSX open no CHD that refers to a parent
at all; that is :mod:`atlas.psx_region`'s rule, read off :class:`ChdFacts`.

What a crafted file could inflate is bounded here, and a file past a bound
answers :class:`~atlas.psx_disc.Unreadable`: the hunk size, the hunk count, the
compressed map, the metadata chain, the files of a parent's folder, the steps
from a hunk to the bytes it copies — within a file and across parents
together — and the output a zstd stream may declare. One budget covers a whole
read through every parent: map entries decoded, hunk bytes held, and candidate
files looked at and opened while looking for a parent. Every bound lies far
beyond what a CD holds, but for two: the map budget refuses a chain of more
than about 22 parents each the size of a full CD, where DuckStation goes to 32,
and the budget of files looked at refuses a chain whose depth times its
folder's file count passes 2**18, such as 5 parents in a folder of 65 536 files.
"""

from __future__ import annotations

import binascii
import os
import re
from contextlib import ExitStack
from dataclasses import dataclass
from typing import BinaryIO, Callable

from . import psx_disc, squashfs

try:
    import lzma
except ImportError:  # a frozen runtime can ship without _lzma
    lzma = None
try:
    import zlib
except ImportError:  # and without zlib
    zlib = None

CHD_SUFFIX = ".chd"

# The codecs each emulator's libchdr carries, by tag — the codec_interfaces
# table every slot of a version 5 header is looked up in, a slot naming any
# other refused at the open (libchdr_chd.c:1181-1313 and :1877-1890 here;
# SwanStation dep/libchdr/src/libchdr_chd.c:1014-1123 and :1686-1699 at 4d309c0,
# which carries neither zstd codec; Beetle PSX
# deps/libchdr/src/libchdr_chd.c:1196-1326 at d6383bf).
DUCKSTATION_CODECS = frozenset({"zlib", "lzma", "huff", "flac", "zstd", "cdzl", "cdlz", "cdfl", "cdzs"})
SWANSTATION_CODECS = frozenset({"zlib", "lzma", "huff", "flac", "cdzl", "cdlz", "cdfl"})
BEETLE_CODECS = frozenset({"zlib", "lzma", "huff", "flac", "zstd", "cdzl", "cdlz", "cdfl", "cdzs"})
CODEC_CDLZ = "cdlz"
CODEC_CDZL = "cdzl"
CODEC_CDZS = "cdzs"

_TAG = b"MComprHD"
_VERSION = 5
_HEADER_SIZE = 124
# The header length each older version declares (chd.h:179-185).
_OLDER_HEADER_SIZES = {1: 76, 2: 80, 3: 120, 4: 108}
# Where an older header keeps its SHA-1, the one hash a parent is matched by
# here: versions 1 and 2 keep none, so theirs reads as all zero (:2566-2617).
_OLDER_SHA1_AT = {3: 80, 4: 48}
_NULL_SHA1 = bytes(20)

FRAME_SIZE = 2448
SECTOR_SIZE = psx_disc.RAW_SECTOR_SIZE
SUBCODE_SIZE = FRAME_SIZE - SECTOR_SIZE
# DuckStation's own depth limit for a chain of parents (cd_image_chd.cpp:79).
MAX_PARENTS = 32

# atlas's bounds, each far past a CD: a hunk of a MiB (chdman writes 8 frames,
# 19584 bytes), half a million hunks in one file, a 16 MiB compressed map,
# 4096 metadata entries, 65536 files in a parent's folder, 64 steps from a
# hunk to the bytes it copies — within a file and across parents together, a
# full chain of DuckStation's 32 parents with room for copies — and 8 MiB out
# of one zstd stream. One read, through every parent, spends at most a million
# map entries, 32 MiB of hunks held (an identity needs a handful of hunks,
# chdman's are 19584 bytes), and while looking for parents 2^18 candidate files
# looked at and 8192 opened.
_MAX_HUNK_BYTES = 1 << 20
_MAX_HUNKS = 1 << 19
_MAX_MAP_BYTES = 16 << 20
_MAX_METADATA = 4096
_MAX_FOLDER_FILES = 1 << 16
_MAX_STEPS = 64
_MAX_ZSTD_OUTPUT = 8 << 20
_SPENT_MAP_ENTRIES = "map entries"
_SPENT_HUNK_BYTES = "bytes of hunks held"
_SPENT_LOOKS = "candidate files looked at"
_SPENT_OPENS = "candidate files opened"
_BUDGET = {_SPENT_MAP_ENTRIES: 1 << 20, _SPENT_HUNK_BYTES: 32 << 20, _SPENT_LOOKS: 1 << 18, _SPENT_OPENS: 8192}

# The map's entry types (libchdr_chd.c:116-149).
_TYPE_CODEC_LAST = 3
_TYPE_NONE = 4
_TYPE_SELF = 5
_TYPE_PARENT = 6
_RLE_SMALL = 7
_RLE_LARGE = 8
_SELF_0 = 9
_SELF_1 = 10
_PARENT_SELF = 11
_PARENT_0 = 12
_PARENT_1 = 13

_METADATA_HEADER_SIZE = 16
_CHT2 = b"CHT2"
_CHTR = b"CHTR"
# What DuckStation reads of one metadata entry (cd_image_chd.cpp:274-283).
_METADATA_TEXT = 256
# ParseTrackModeString (cd_image_chd.cpp:34-54).
_TRACK_MODES = frozenset(
    {"MODE2_FORM_MIX", "MODE2_FORM1", "MODE2_FORM2", "MODE2_RAW", "MODE1_RAW", "MODE1", "MODE2", "AUDIO"}
)

# sscanf over the two track formats (chd.h:244-247): a space in the format
# takes any run of C whitespace, ``%d`` a signed decimal after whitespace, ``%s``
# a run of non-space after whitespace that gives nothing back.
_WS = r"[ \t\n\v\f\r]*"
_INT = _WS + r"([+-]?[0-9]++)"
_STR = _WS + r"([^ \t\n\v\f\r]++)"
_CHTR_FORMAT = re.compile(
    ("TRACK:" + _INT + _WS + "TYPE:" + _STR + _WS + "SUBTYPE:" + _STR + _WS + "FRAMES:" + _INT).encode("ascii")
)
_CHT2_FORMAT = re.compile(
    (
        "TRACK:" + _INT + _WS + "TYPE:" + _STR + _WS + "SUBTYPE:" + _STR + _WS + "FRAMES:" + _INT + _WS
        + "PREGAP:" + _INT + _WS + "PGTYPE:" + _STR + _WS + "PGSUB:" + _STR + _WS + "POSTGAP:" + _INT
    ).encode("ascii")
)

Lister = Callable[[str], list[str]]
"""The regular files of a folder as full paths, in directory order — a symlink followed, nothing else listed."""


class ParentNotFound(Exception):
    """The CHD refers to a parent, and DuckStation's search of its folder finds none that opens."""


@dataclass(frozen=True, slots=True)
class ChdFacts:
    """What an emulator's own CHD open decides on, read off the header before any hunk.

    ``codecs`` holds the tags of the header's non-empty codec slots in slot
    order — an emulator whose libchdr lacks one of them does not open the file.
    ``requires_parent`` says whether the header names a parent: a version 5
    header whose parent SHA-1 is not all zero (libchdr_chd.c:1795-1803).
    """

    codecs: tuple[str, ...]
    requires_parent: bool


@dataclass(frozen=True, slots=True)
class _Header:
    version: int
    compression: tuple[int, int, int, int]
    logical_bytes: int
    map_offset: int
    meta_offset: int
    hunk_bytes: int
    unit_bytes: int
    sha1: bytes
    parent_sha1: bytes

    @property
    def hunk_count(self) -> int:
        return (self.logical_bytes + self.hunk_bytes - 1) // self.hunk_bytes

    @property
    def compressed(self) -> bool:
        return self.compression[0] != 0

    @property
    def requires_parent(self) -> bool:
        return self.parent_sha1 != _NULL_SHA1

    def facts(self) -> ChdFacts:
        return ChdFacts(tuple(_tag(value) for value in self.compression if value), self.requires_parent)


@dataclass(frozen=True, slots=True)
class _Older:
    """A CHD of version 1 to 4: its SHA-1, as far as its header keeps one, and nothing atlas reads past it."""

    version: int
    sha1: bytes


def _tag(value: int) -> str:
    return value.to_bytes(4, "big").decode("latin-1")


def _u(data: bytes, start: int, size: int) -> int:
    return int.from_bytes(data[start : start + size], "big")


def _read_exactly(handle: BinaryIO, offset: int, size: int) -> bytes | None:
    """*size* bytes at *offset*, or ``None`` where the file ends first.

    The end is measured before the seek, because an offset far past it is one
    a filesystem refuses to seek to at all.
    """
    if offset + size > handle.seek(0, os.SEEK_END):
        return None
    handle.seek(offset)
    data = handle.read(size)
    return data if len(data) == size else None


def _header(handle: BinaryIO) -> _Header | _Older:
    """``header_read`` and ``header_validate``: the tag, the version and its length, then the version 5 fields.

    Raises :class:`~atlas.psx_disc.NotADisc` where libchdr refuses the header.
    A version 1 to 4 header is returned as :class:`_Older`, which nothing here
    reads past.
    """
    raw = _read_exactly(handle, 0, _HEADER_SIZE)
    if raw is None or not raw.startswith(_TAG):
        raise psx_disc.NotADisc("the file is no CHD: shorter than a header, or without the MComprHD tag (:2533-2539)")
    length, version = _u(raw, 8, 4), _u(raw, 12, 4)
    if version in _OLDER_HEADER_SIZES and length == _OLDER_HEADER_SIZES[version]:
        at = _OLDER_SHA1_AT.get(version)
        return _Older(version, _NULL_SHA1 if at is None else raw[at : at + 20])
    if version != _VERSION or length != _HEADER_SIZE:
        raise psx_disc.NotADisc(f"a CHD header libchdr refuses: version {version}, length {length} (:2546-2557)")
    header = _Header(
        version=version,
        compression=(_u(raw, 16, 4), _u(raw, 20, 4), _u(raw, 24, 4), _u(raw, 28, 4)),
        logical_bytes=_u(raw, 32, 8),
        map_offset=_u(raw, 40, 8),
        meta_offset=_u(raw, 48, 8),
        hunk_bytes=_u(raw, 56, 4),
        unit_bytes=_u(raw, 60, 4),
        sha1=raw[84:104],
        parent_sha1=raw[104:124],
    )
    if header.hunk_bytes == 0 or header.unit_bytes == 0:
        raise psx_disc.NotADisc("a CHD header with no hunk or unit size, which libchdr refuses (:2630-2636)")
    return header


def read_facts(path: str, opener: psx_disc.Opener) -> ChdFacts:
    """The facts an emulator's open decides on — the header only, so they stand whatever a later read finds.

    Raises :class:`~atlas.psx_disc.NotADisc` for a header libchdr refuses,
    :class:`~atlas.psx_disc.FormatNotRead` for a version before 5, and the
    opener's ``OSError``.
    """
    with opener(path) as handle:
        header = _header(handle)
    if isinstance(header, _Older):
        raise psx_disc.FormatNotRead(f"a CHD of version {header.version}; atlas reads version 5 only")
    return header.facts()


# ── The map ────────────────────────────────────────────────────────────────


class _Bits:
    """libchdr's bitstream (libchdr_bitstream.c:19-89): a 32-bit buffer refilled a byte at a time, zeros past the end."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._buffer = 0
        self._held = 0
        self._offset = 0

    def peek(self, count: int) -> int:
        if count == 0:
            return 0
        if count > self._held:
            while self._held <= 24:
                if self._offset < len(self._data):
                    # A read wider than the buffer holds leaves a negative
                    # count, and the C's shift then runs past 32 bits, which the
                    # unsigned buffer cuts off.
                    self._buffer = (self._buffer | self._data[self._offset] << (24 - self._held)) & 0xFFFFFFFF
                self._offset += 1
                self._held += 8
        return self._buffer >> (32 - count)

    def remove(self, count: int) -> None:
        self._buffer = (self._buffer << count) & 0xFFFFFFFF
        self._held -= count

    def read(self, count: int) -> int:
        value = self.peek(count)
        self.remove(count)
        return value

    def overflowed(self) -> bool:
        return self._offset - self._held // 8 > len(self._data)


_MAP_CODES = 16
_MAP_MAX_BITS = 8


def _huffman_lookup(bits: _Bits) -> list[tuple[int, int] | None]:
    """``huffman_import_tree_rle`` for the map's 16 codes of at most 8 bits (libchdr_huffman.c:181-237, :484-550).

    The lookup holds ``(symbol, length)`` per 8-bit window, ``None`` where no
    code covers it — a window libchdr leaves as whatever its allocation held.
    Raises :class:`~atlas.psx_disc.NotADisc` where libchdr refuses the tree.
    """
    lengths = _code_lengths(bits)
    lookup = _canonical_lookup(lengths)
    if bits.overflowed():
        raise psx_disc.NotADisc("the map's code table runs past the compressed map (libchdr_huffman.c:236)")
    return lookup


def _code_lengths(bits: _Bits) -> list[int]:
    """The RLE-coded code lengths (libchdr_huffman.c:196-225): a 1 escapes a literal 1 or a run of one length."""
    lengths: list[int] = []
    while len(lengths) < _MAP_CODES:
        length = bits.read(4)
        if length != 1:
            lengths.append(length)
            continue
        length = bits.read(4)
        if length == 1:
            lengths.append(1)
            continue
        repeat = bits.read(4) + 3
        if repeat + len(lengths) > _MAP_CODES:
            raise psx_disc.NotADisc("the map's code lengths run past its 16 codes (libchdr_huffman.c:214-216)")
        lengths.extend([length] * repeat)
    return lengths


def _canonical_lookup(lengths: list[int]) -> list[tuple[int, int] | None]:
    """``huffman_assign_canonical_codes`` and ``huffman_build_lookup_table`` (libchdr_huffman.c:484-550)."""
    if any(length > _MAP_MAX_BITS for length in lengths):
        raise psx_disc.NotADisc("a map code longer than 8 bits (libchdr_huffman.c:494-495)")
    histogram = [0] * 33
    for length in lengths:
        histogram[length] += 1
    start = 0
    for length in range(32, 0, -1):
        following = (start + histogram[length]) >> 1
        if length != 1 and following * 2 != start + histogram[length]:
            raise psx_disc.NotADisc("the map's code lengths form no canonical code (libchdr_huffman.c:501-507)")
        histogram[length] = start
        start = following
    lookup: list[tuple[int, int] | None] = [None] * (1 << _MAP_MAX_BITS)
    for symbol, length in enumerate(lengths):
        if length == 0:
            continue
        code = histogram[length]
        histogram[length] += 1
        shift = _MAP_MAX_BITS - length
        for window in range(code << shift, min((code + 1) << shift, len(lookup))):
            lookup[window] = (symbol, length)
    return lookup


def _decode_one(lookup: list[tuple[int, int] | None], bits: _Bits) -> int:
    """``huffman_decode_one`` (libchdr_huffman.c:162-173)."""
    entry = lookup[bits.peek(_MAP_MAX_BITS)]
    if entry is None:
        raise psx_disc.NotADisc("the map holds a code its own table does not define")
    bits.remove(entry[1])
    return entry[0]


@dataclass(frozen=True, slots=True)
class _Entry:
    kind: int
    length: int
    offset: int
    crc: int


def _map_types(lookup: list[tuple[int, int] | None], bits: _Bits, count: int) -> list[int]:
    """The first pass of ``decompress_v5_map`` (:1612-1627): each hunk's type, with its two run-length codes."""
    kinds: list[int] = []
    repeat, last = 0, 0
    while len(kinds) < count:
        if repeat > 0:
            kinds.append(last)
            repeat -= 1
            continue
        value = _decode_one(lookup, bits)
        if value == _RLE_SMALL:
            kinds.append(last)
            repeat = 2 + _decode_one(lookup, bits)
        elif value == _RLE_LARGE:
            kinds.append(last)
            repeat = 2 + 16 + (_decode_one(lookup, bits) << 4)
            repeat += _decode_one(lookup, bits)
        else:
            kinds.append(value)
            last = value
    return kinds


class _MapState:
    """The running values the map's second pass carries from one hunk to the next (:1630-1690)."""

    def __init__(self, header: _Header, first_offset: int) -> None:
        self.header = header
        self.offset = first_offset
        self.last_self = 0
        self.last_parent = 0

    def entry(self, number: int, kind: int, bits: _Bits, widths: tuple[int, int, int]) -> _Entry:
        length_bits, self_bits, parent_bits = widths
        offset, length, crc = self.offset, 0, 0
        if kind <= _TYPE_CODEC_LAST:
            length = bits.read(length_bits)
            self.offset += length
            crc = bits.read(16)
        elif kind == _TYPE_NONE:
            length = self.header.hunk_bytes
            self.offset += length
            crc = bits.read(16)
        elif kind == _TYPE_SELF:
            offset = self.last_self = bits.read(self_bits)
        elif kind == _TYPE_PARENT:
            offset = self.last_parent = bits.read(parent_bits)
        elif kind in (_SELF_0, _SELF_1):
            if kind == _SELF_1:
                self.last_self = (self.last_self + 1) & 0xFFFFFFFF
            kind, offset = _TYPE_SELF, self.last_self
        elif kind == _PARENT_SELF:
            kind = _TYPE_PARENT
            offset = self.last_parent = number * self.header.hunk_bytes // self.header.unit_bytes
        elif kind in (_PARENT_0, _PARENT_1):
            if kind == _PARENT_1:
                self.last_parent += self.header.hunk_bytes // self.header.unit_bytes
            kind, offset = _TYPE_PARENT, self.last_parent
        # The raw map keeps the length in 24 bits and the offset in 48 (:1682-1689).
        return _Entry(kind, length & 0xFFFFFF, offset & 0xFFFFFFFFFFFF, crc)


def _raw_map(entries: list[_Entry]) -> bytes:
    return b"".join(
        bytes((entry.kind,))
        + entry.length.to_bytes(3, "big")
        + entry.offset.to_bytes(6, "big")
        + entry.crc.to_bytes(2, "big")
        for entry in entries
    )


def _compressed_map(handle: BinaryIO, header: _Header) -> list[_Entry]:
    """``decompress_v5_map`` (:1537-1702): the types, then each entry, then the CRC over the raw map."""
    head = _read_exactly(handle, header.map_offset, 16)
    if head is None:
        raise psx_disc.NotADisc("the map's header lies past the end of the file")
    map_bytes, first_offset, map_crc = _u(head, 0, 4), _u(head, 4, 6), _u(head, 10, 2)
    widths = (head[12], head[13], head[14])
    if map_bytes > _MAX_MAP_BYTES:
        raise psx_disc.Unreadable(f"a compressed map of {map_bytes} bytes, past atlas's bound of {_MAX_MAP_BYTES}")
    if max(widths) >= 32:
        raise psx_disc.Unreadable("a map field of 32 bits or more, which libchdr's 32-bit reads cannot carry")
    compressed = _read_exactly(handle, header.map_offset + 16, map_bytes)
    if compressed is None:
        raise psx_disc.NotADisc("the compressed map runs past the end of the file")
    bits = _Bits(compressed)
    kinds = _map_types(_huffman_lookup(bits), bits, header.hunk_count)
    state = _MapState(header, first_offset)
    entries = [state.entry(number, kind, bits, widths) for number, kind in enumerate(kinds)]
    if binascii.crc_hqx(_raw_map(entries), 0xFFFF) != map_crc:
        raise psx_disc.NotADisc("the map fails its CRC (:1697-1699)")
    return entries


def _uncompressed_map(handle: BinaryIO, header: _Header) -> list[int]:
    """An uncompressed CHD's map: one 32-bit hunk number per hunk, 0 for none (:1559-1567, :2839-2857)."""
    raw = _read_exactly(handle, header.map_offset, header.hunk_count * 4)
    if raw is None:
        raise psx_disc.NotADisc("the map runs past the end of the file")
    return [_u(raw, index * 4, 4) for index in range(header.hunk_count)]


# ── The codecs ─────────────────────────────────────────────────────────────


def _lzma_dictionary(size: int) -> int:
    """``LzmaEncProps_Normalize`` at level 9 for *size* bytes (dep/lzma/src/LzmaEnc.c:52-68), as lzma_codec_init sets it."""
    for shift in range(11, 31):
        if size <= 2 << shift:
            return 2 << shift
        if size <= 3 << shift:
            return 3 << shift
    return 1 << 26


def _unlzma(data: bytes, size: int) -> bytes:
    """A raw LZMA stream with the properties lzma_codec_init derives (:563-611), no end mark."""
    if lzma is None:
        raise psx_disc.CodecUnavailable("cdlz needs the lzma module, which this runtime does not have")
    filters = [{"id": lzma.FILTER_LZMA1, "dict_size": _lzma_dictionary(size), "lc": 3, "lp": 0, "pb": 2}]
    try:
        return lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filters).decompress(data, size)
    except lzma.LZMAError as error:
        raise psx_disc.Unreadable("an LZMA stream that does not decode") from error


def _inflate(data: bytes, size: int) -> bytes:
    """Raw deflate, as zlib_codec_init sets it up (:3116-3142, :3168-3190)."""
    if zlib is None:
        raise psx_disc.CodecUnavailable("cdzl and cdlz need the zlib module, which this runtime does not have")
    try:
        return zlib.decompressobj(-15).decompress(data, size)
    except zlib.error as error:
        raise psx_disc.Unreadable("a deflate stream that does not decode") from error


def _zstd_output_bound(data: bytes) -> int | None:
    """The most the zstd frames in *data* can produce, from their block headers — ``None`` where they do not parse.

    RFC 8878 sections 3.1.1-3.1.1.2: a raw or RLE block states its size, and a
    compressed one produces at most the block maximum, the smaller of the
    window and 128 KiB. Skippable frames produce nothing.
    """
    total, position = 0, 0
    while position < len(data):
        magic = int.from_bytes(data[position : position + 4], "little") if len(data) - position >= 4 else None
        if magic is not None and magic & 0xFFFFFFF0 == 0x184D2A50 and len(data) - position >= 8:
            position += 8 + int.from_bytes(data[position + 4 : position + 8], "little")
            continue
        if magic != 0xFD2FB528 or position + 5 > len(data):
            return None
        frame = _zstd_frame(data, position + 4)
        if frame is None:
            return None
        position, produced = frame
        total += produced
    return total


def _zstd_frame(data: bytes, position: int) -> tuple[int, int] | None:
    """One frame from its descriptor byte: where it ends and the most its blocks produce (RFC 8878 3.1.1.1-3.1.1.2)."""
    descriptor = data[position]
    single = descriptor >> 5 & 1
    position += 1
    window = 0
    if not single and position < len(data):
        exponent, mantissa = data[position] >> 3, data[position] & 7
        base = 1 << (10 + exponent)
        window = base + (base >> 3) * mantissa
        position += 1
    position += (0, 1, 2, 4)[descriptor & 3]
    content_size_bytes = (1 if single else 0, 2, 4, 8)[descriptor >> 6]
    if single:
        window = int.from_bytes(data[position : position + content_size_bytes], "little")
        window += 256 if content_size_bytes == 2 else 0
    position += content_size_bytes
    blocks = _zstd_blocks(data, position, min(window, 128 * 1024))
    if blocks is None:
        return None
    position, produced = blocks
    return position + (4 if descriptor >> 2 & 1 else 0), produced


def _zstd_blocks(data: bytes, position: int, maximum: int) -> tuple[int, int] | None:
    """A frame's blocks up to the last: where they end and the most they produce."""
    produced = 0
    while True:
        if position + 3 > len(data):
            return None
        block = int.from_bytes(data[position : position + 3], "little")
        last, kind, size = block & 1, block >> 1 & 3, block >> 3
        if kind == 3:
            return None
        produced += size if kind < 2 else maximum
        position += 3 + (1 if kind == 1 else size)
        if last:
            return position, produced


def _unzstd(data: bytes, size: int) -> bytes:
    """A zstd stream through the provider :func:`atlas.squashfs.zstd_codec` resolves, its output bounded first."""
    codec = squashfs.zstd_codec()
    if codec is None:
        raise psx_disc.CodecUnavailable("cdzs needs a zstd provider, and this runtime has none")
    bound = _zstd_output_bound(data)
    if bound is None or bound > _MAX_ZSTD_OUTPUT:
        raise psx_disc.Unreadable("a zstd stream whose blocks do not parse or would decode past atlas's bound")
    try:
        return bytes(codec.decompress(data))[:size]
    except Exception as error:  # a provider's own error type is its own
        raise psx_disc.Unreadable("a zstd stream that does not decode") from error


_CD_CODECS: dict[str, tuple[Callable[[bytes, int], bytes], Callable[[bytes, int], bytes]]] = {
    CODEC_CDLZ: (_unlzma, _inflate),
    CODEC_CDZL: (_inflate, _inflate),
    CODEC_CDZS: (_unzstd, _unzstd),
}


# The rebuilt parity (libchdr_cdrom.c:104-426): GF(2^8) over x^8+x^4+x^3+x^2+1,
# P over 86 columns of 24 bytes and Q over 52 diagonals of 43, each counted
# from the sector header at byte 12.
_ECC_P_OFFSET = 0x81C
_ECC_Q_OFFSET = _ECC_P_OFFSET + 2 * 86
_SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
_ECC_LOW = bytes(((value << 1) ^ (0x11D if value & 0x80 else 0)) & 0xFF for value in range(256))
_ECC_HIGH = bytearray(256)
for _value in range(256):
    _ECC_HIGH[_value ^ _ECC_LOW[_value]] = _value
_P_ROWS = tuple(tuple(byte + 86 * index for index in range(24)) for byte in range(86))
_Q_ROWS = tuple(
    tuple(2 * (((byte >> 1) * 43 + index * 44) % 1118) + (byte & 1) for index in range(43)) for byte in range(52)
)


def _ecc_pair(sector: bytearray, row: tuple[int, ...]) -> tuple[int, int]:
    """``ecc_compute_bytes`` (:356-368), a Mode 2 header read as zeros (``ecc_source_byte``, :336-340)."""
    first = second = 0
    mode2 = sector[15] == 2
    for offset in row:
        value = 0 if mode2 and offset < 4 else sector[12 + offset]
        first ^= value
        second ^= value
        first = _ECC_LOW[first]
    first = _ECC_HIGH[_ECC_LOW[first] ^ second]
    return first, second ^ first


def _ecc_generate(sector: bytearray) -> None:
    """``ecc_generate`` (:416-426): P first, then Q, which covers P."""
    for byte, row in enumerate(_P_ROWS):
        sector[_ECC_P_OFFSET + byte], sector[_ECC_P_OFFSET + 86 + byte] = _ecc_pair(sector, row)
    for byte, row in enumerate(_Q_ROWS):
        sector[_ECC_Q_OFFSET + byte], sector[_ECC_Q_OFFSET + 52 + byte] = _ecc_pair(sector, row)


def _cd_hunk(codec: str, source: bytes, hunk_bytes: int) -> bytes:
    """``cdlz``, ``cdzl`` and ``cdzs`` decompression (:689-732, :772-815, :1132-1175): sector bytes and subcode
    apart, then each frame reassembled, its sync and parity rebuilt where the ECC bitmap says they were stripped.

    The C ignores what the two decompressions return and lets the hunk's CRC
    judge; a stream that yields less here is :class:`~atlas.psx_disc.Unreadable`
    at once, since the bytes libchdr would then check are a buffer's leftovers.
    """
    base_codec, subcode_codec = _CD_CODECS[codec]
    frames = hunk_bytes // FRAME_SIZE
    length_bytes = 2 if hunk_bytes < 65536 else 3
    ecc_bytes = (frames + 7) // 8
    header_bytes = ecc_bytes + length_bytes
    if len(source) < header_bytes:
        raise psx_disc.Unreadable(f"a {codec} hunk shorter than its own header")
    base_length = _u(source, ecc_bytes, length_bytes)
    sectors = base_codec(source[header_bytes : header_bytes + base_length], frames * SECTOR_SIZE)
    subcode = subcode_codec(source[header_bytes + base_length :], frames * SUBCODE_SIZE)
    if len(sectors) != frames * SECTOR_SIZE or len(subcode) != frames * SUBCODE_SIZE:
        raise psx_disc.Unreadable(f"a {codec} hunk decodes short")
    hunk = bytearray()
    for frame in range(frames):
        sector = bytearray(sectors[frame * SECTOR_SIZE : (frame + 1) * SECTOR_SIZE])
        if source[frame // 8] & (1 << (frame % 8)):
            sector[: len(_SYNC)] = _SYNC
            _ecc_generate(sector)
        hunk += sector + subcode[frame * SUBCODE_SIZE : (frame + 1) * SUBCODE_SIZE]
    return bytes(hunk)


# ── One opened file ────────────────────────────────────────────────────────


class _Work:
    """What one read may spend, summed over the CHD and every parent it reads through.

    A chain multiplies work — a parent's map is decoded once per file, and a
    hunk read through a chain decodes a hunk in each file it passes — so the
    bounds are one budget for the whole read rather than one per file: map
    entries decoded, bytes of hunks held, and candidate files looked at and
    opened while looking for a parent. A hunk is decoded once per open of a
    file — per file and level — and kept, so a hunk two straddling reads share
    is not decoded twice; every buffer kept is charged, the hunks a straddle
    joins and the zeros an uncompressed file stands for among them. Spending
    past one is :class:`~atlas.psx_disc.Unreadable`.
    """

    def __init__(self) -> None:
        self._spent = dict.fromkeys(_BUDGET, 0)

    def spend(self, what: str, amount: int = 1) -> None:
        self._spent[what] += amount
        if self._spent[what] > _BUDGET[what]:
            raise psx_disc.Unreadable(f"a read past atlas's bound of {_BUDGET[what]} {what}")


class _Image:
    """One opened CHD file: its handle, its header and its map, decoded once however many chains pass through it."""

    def __init__(self, handle: BinaryIO, header: _Header, work: _Work) -> None:
        self.handle = handle
        self.header = header
        if header.hunk_bytes > _MAX_HUNK_BYTES or header.hunk_count > _MAX_HUNKS:
            raise psx_disc.Unreadable(
                f"a CHD of {header.hunk_count} hunks of {header.hunk_bytes} bytes, past atlas's bounds of "
                f"{_MAX_HUNKS} hunks of {_MAX_HUNK_BYTES} bytes"
            )
        work.spend(_SPENT_MAP_ENTRIES, header.hunk_count)
        self.entries: list[_Entry] | list[int] = (
            _compressed_map(handle, header) if header.compressed else _uncompressed_map(handle, header)
        )


class _Chd:
    """A file read through the parent its open settled on, and the hunks it was asked for, kept.

    ``hunk_read_into_memory`` (:2750-2972) follows a copy within the file and a
    hunk of the parent by calling itself; here the walk is a loop with one
    count of steps across both, so no chain reaches Python's recursion limit.
    Only a hunk whose units straddle two of the parent's (:2946-2965) reads
    those two through the parent's own walk, at most one level per parent.
    """

    def __init__(self, image: _Image, parent: _Chd | None, work: _Work) -> None:
        self.image = image
        self.header = image.header
        self.parent = parent
        self._work = work
        self._kept: dict[int, bytes] = {}

    def hunk(self, number: int) -> bytes:
        node: _Chd = self
        for _ in range(_MAX_STEPS):
            if number in node._kept:
                return node._kept[number]
            if number >= node.header.hunk_count:
                raise psx_disc.Unreadable(f"hunk {number} lies past the CHD's {node.header.hunk_count} hunks")
            step = node._step(number)
            if isinstance(step, bytes):
                node._work.spend(_SPENT_HUNK_BYTES, len(step))
                node._kept[number] = step
                return step
            node, number = step
        raise psx_disc.Unreadable(f"a hunk that copies one hunk from another more than {_MAX_STEPS} steps deep")

    def _step(self, number: int) -> bytes | tuple[_Chd, int]:
        """One hunk's map entry: its bytes, or the file and hunk it copies."""
        if not self.header.compressed:
            return self._uncompressed(number)
        entry = self.image.entries[number]
        assert isinstance(entry, _Entry)
        if entry.kind <= _TYPE_CODEC_LAST:
            return self._checked(number, self._decoded(number, entry), entry.crc)
        if entry.kind == _TYPE_NONE:
            return self._checked(number, self._stored(number, entry.offset), entry.crc)
        if entry.kind == _TYPE_SELF:
            return self, entry.offset
        if entry.kind == _TYPE_PARENT:
            return self._from_parent(entry.offset)
        raise psx_disc.Unreadable(f"hunk {number} has a map type ({entry.kind}) libchdr reads nothing for")

    def _uncompressed(self, number: int) -> bytes | tuple[_Chd, int]:
        """An uncompressed file's hunk (:2839-2857): stored where its map says, else the parent's, else zeros."""
        entry = self.image.entries[number]
        assert isinstance(entry, int)
        if entry:
            return self._stored(number, entry * self.header.hunk_bytes)
        if self.parent is not None:
            return self.parent, number
        return bytes(self.header.hunk_bytes)

    def _stored(self, number: int, offset: int) -> bytes:
        data = _read_exactly(self.image.handle, offset, self.header.hunk_bytes)
        if data is None:
            raise psx_disc.Unreadable(f"hunk {number} lies past the end of the file")
        return data

    def _checked(self, number: int, data: bytes, crc: int) -> bytes:
        if binascii.crc_hqx(data, 0xFFFF) != crc:
            raise psx_disc.Unreadable(f"hunk {number} fails its CRC")
        return data

    def _decoded(self, number: int, entry: _Entry) -> bytes:
        tag = self.header.compression[entry.kind]
        if tag == 0:
            raise psx_disc.Unreadable(f"hunk {number} names an empty codec slot")
        codec = _tag(tag)
        if codec not in _CD_CODECS:
            raise psx_disc.FormatNotRead(f"hunk {number} is compressed with {codec!r}, which atlas does not read")
        if entry.length > self.header.hunk_bytes:
            raise psx_disc.Unreadable(f"hunk {number} is longer compressed than a hunk, which libchdr's buffer is")
        source = _read_exactly(self.image.handle, entry.offset, entry.length)
        if source is None:
            raise psx_disc.Unreadable(f"hunk {number} lies past the end of the file")
        return _cd_hunk(codec, source, self.header.hunk_bytes)

    def _from_parent(self, unit: int) -> bytes | tuple[_Chd, int]:
        """A hunk's worth of units from the parent (:2938-2967), across two of its hunks where they straddle."""
        parent = self.parent
        if parent is None:
            raise psx_disc.Unreadable("a hunk of the parent in a CHD that names none (:2939-2940)")
        if parent.header.hunk_bytes != self.header.hunk_bytes or parent.header.unit_bytes != self.header.unit_bytes:
            raise psx_disc.Unreadable("a parent whose hunk or unit size differs, which libchdr reads past its buffer")
        # units_in_hunk is a UINT8 in the C (:2941).
        per_hunk = (self.header.hunk_bytes // self.header.unit_bytes) & 0xFF
        if per_hunk == 0:
            raise psx_disc.Unreadable("a hunk of no whole unit, which libchdr divides by")
        first, within = divmod(unit, per_hunk)
        if within == 0:
            return parent, first
        cut = within * self.header.unit_bytes
        return parent.hunk(first)[cut:] + parent.hunk(first + 1)[:cut]


# ── Finding the parent the way DuckStation finds it ────────────────────────


def _directory(path: str) -> str:
    """``Path::GetDirectory`` (common/file_system.cpp:636-643 at 1f6de046b): up to the last ``/``, empty without one.

    Unlike :func:`os.path.dirname` it leaves nothing of a root: a file directly
    under ``/`` has the empty directory, which no listing opens.
    """
    return path[: max(path.rfind("/"), 0)]


def _wildcard_dot(name: str) -> bool:
    """``StringUtil::WildcardMatch(name, "*.*")`` (common/string_util.cpp:22-116 at 1f6de046b): a dot anywhere."""
    return "." in name


def _matches(child: _Header, candidate: _Header | _Older) -> bool:
    """``chd_is_matching_parent`` (:2292-2311) for a version 5 child, whose parent MD5 is always empty.

    A candidate whose own SHA-1 is all zero matches any child, as the C has it.
    """
    return candidate.sha1 == _NULL_SHA1 or candidate.sha1 == child.parent_sha1


class _Folder:
    """The child's folder as DuckStation's search walks it (cd_image_chd.cpp:177-214).

    ``FindFiles(dir, "*.*", FILES | HIDDEN)`` lists every file whose name holds
    a dot, hidden ones included, in directory order (file_system.cpp:2247-2376).
    The filter after it (:186) compares an extension taken after the last dot
    with ``.chd``, which such an extension never ends with, so it skips
    nothing: every listed file is tried.

    Before that walk DuckStation consults ``s_chd_hash_cache`` (:56, :147-175),
    a process-wide list of every header a walk has read, never cleared; the
    game list's scan opens the images its own cache does not already hold
    (``game_list.cpp:505-509``, ``:250``), so it can be filled before a launch.
    It takes the first cached file whose folder equals the child's without
    regard to case (``EqualNoCase``, :150) and whose header still matches, and
    only where that one does not open does it walk the folder. The cache is not
    modelled here. Where the folder is the one the child is in, it names a file
    the walk also finds; but it keeps the folder order of when it was filled,
    and it can name a file in a folder that differs only in case — ``PSX/`` for
    a child in ``psx/`` — from which DuckStation then reads a parent where this
    answers that none was found.

    The search recurses as DuckStation's does, level by level, and that is
    followed rather than shortened. A file that matches itself — a null SHA-1
    matches every child (``chd_is_matching_parent``, libchdr_chd.c:2292-2311,
    and the open's own check at :1814-1818) — is taken as its own parent at the
    next level, again and again, until the depth limit refuses it (:124-129)
    and the next match behind it is taken. The chain then holds that file at
    every level, and a hunk of the parent read through it reads the file's own
    stored hunks one level down. So an open is kept per file and level — a
    file opened at two levels is two links of the chain — over one image per
    file, opened and its map decoded once; a failed open is kept the same way,
    so no level retries it. Every candidate looked at and every open is charged
    to the read's budget, which ends a ring of files matching each other, or a
    folder too large to walk at every level, in a bound rather than in the
    exponential walk DuckStation itself would make.
    """

    def __init__(self, directory: str, opener: psx_disc.Opener, lister: Lister, stack: ExitStack, work: _Work) -> None:
        listed = lister(directory)
        if len(listed) > _MAX_FOLDER_FILES:
            raise psx_disc.Unreadable(f"a folder of more than {_MAX_FOLDER_FILES} files to search for a parent")
        self.paths = [path for path in listed if _wildcard_dot(os.path.basename(path))]
        self._opener = opener
        self._stack = stack
        self._work = work
        self._headers: dict[str, _Header | _Older | None] = {}
        self._images: dict[str, _Image | None] = {}
        self._opened: dict[tuple[str, int], _Chd | None] = {}

    def header(self, path: str) -> _Header | _Older | None:
        """The file's header as ``chd_read_header_file`` reads it, or ``None`` where it does not (:191-194)."""
        if path not in self._headers:
            try:
                with self._opener(path) as handle:
                    self._headers[path] = _header(handle)
            except (OSError, psx_disc.NotADisc):
                self._headers[path] = None
        return self._headers[path]

    def parent_of(self, child: _Header, level: int) -> _Chd | None:
        """``OpenCHD``'s search for the CHD at *level* (:124-221): the first matching file that opens, or ``None``."""
        if level >= MAX_PARENTS:
            return None
        for path in self.paths:
            self._work.spend(_SPENT_LOOKS)
            header = self.header(path)
            if header is None or not _matches(child, header):
                continue
            if isinstance(header, _Older):
                raise psx_disc.FormatNotRead(
                    f"the parent DuckStation's search reaches first is a CHD of version {header.version}, which "
                    "atlas does not read"
                )
            opened = self.open(path, header, level + 1)
            if opened is not None:
                return opened
        return None

    def open(self, path: str, header: _Header, level: int) -> _Chd | None:
        """``OpenCHD`` of a candidate at *level* (:106-235), once per file and level, a failure kept as well."""
        key = (path, level)
        if key not in self._opened:
            self._work.spend(_SPENT_OPENS)
            self._opened[key] = self._open(path, header, level)
        return self._opened[key]

    def _open(self, path: str, header: _Header, level: int) -> _Chd | None:
        """Its own parent first, then libchdr's open — ``None`` where either fails."""
        parent = self.parent_of(header, level) if header.requires_parent else None
        if (header.requires_parent and parent is None) or not _opens(header):
            return None
        image = self._image(path, header)
        return None if image is None else _Chd(image, parent, self._work)

    def _image(self, path: str, header: _Header) -> _Image | None:
        """The file opened and its map decoded, once — ``None`` where libchdr's open fails.

        An opened file stays open for the reads through it; one that fails is
        closed at once.
        """
        if path not in self._images:
            self._images[path] = self._opened_image(path, header)
        return self._images[path]

    def _opened_image(self, path: str, header: _Header) -> _Image | None:
        try:
            handle = self._opener(path)
        except OSError:
            return None
        try:
            image = _Image(handle, header, self._work)
        except psx_disc.NotADisc:
            handle.close()
            return None
        except BaseException:
            handle.close()
            raise
        self._stack.callback(handle.close)
        return image


def _opens(header: _Header) -> bool:
    """libchdr's codec checks at the open (:1877-1943): every slot a codec DuckStation carries, a CD codec only over
    whole frames (``cdlz_codec_init`` and its siblings, :652-677, :736-760, :1095-1120)."""
    codecs = header.facts().codecs
    if any(codec not in DUCKSTATION_CODECS for codec in codecs):
        return False
    return header.hunk_bytes % FRAME_SIZE == 0 or not any(codec.startswith("cd") for codec in codecs)


# ── Track 1 ────────────────────────────────────────────────────────────────


def _metadata(handle: BinaryIO, offset: int) -> list[tuple[bytes, int, int]]:
    """The metadata chain as ``metadata_find_entry`` walks it (:3067-3106): tag, offset and length per entry.

    The walk stops at an offset of 0 or a header past the end of the file.
    """
    entries: list[tuple[bytes, int, int]] = []
    while offset != 0:
        if len(entries) >= _MAX_METADATA:
            raise psx_disc.Unreadable(f"a metadata chain longer than atlas's bound of {_MAX_METADATA} entries")
        head = _read_exactly(handle, offset, _METADATA_HEADER_SIZE)
        if head is None:
            break
        entries.append((head[:4], offset, _u(head, 4, 4) & 0xFFFFFF))
        offset = _u(head, 8, 8)
    return entries


def _metadata_text(handle: BinaryIO, offset: int, length: int) -> bytes | None:
    """``chd_get_metadata`` into DuckStation's 256-byte buffer, as the C string sscanf reads (:2378-2383)."""
    data = _read_exactly(handle, offset + _METADATA_HEADER_SIZE, min(_METADATA_TEXT, length))
    return None if data is None else data.split(b"\x00", 1)[0]


@dataclass(frozen=True, slots=True)
class _Track:
    number: int
    mode: str
    subtype: str
    frames: int
    pregap: int
    pregap_type: str


def _s32(text: bytes) -> int:
    value = int(text)
    if not -(2**31) <= value < 2**31:
        raise psx_disc.NotADisc("a track number past what sscanf's %d holds")
    return value


def _tracks(handle: BinaryIO, header: _Header) -> list[_Track]:
    """DuckStation's track loop (cd_image_chd.cpp:272-406): the n-th ``CHT2``, else the n-th ``CHTR``, until neither.

    A track it refuses refuses the disc: a line its format does not fill, a
    number out of order, a mode it does not name, a pregap stored in the file
    and longer than the track.
    """
    chain = _metadata(handle, header.meta_offset)
    by_tag = {tag: [(offset, length) for found, offset, length in chain if found == tag] for tag in (_CHT2, _CHTR)}
    tracks: list[_Track] = []
    while (found := _track_text(handle, by_tag, len(tracks))) is not None:
        text, form = found
        tracks.append(_track(text, form, len(tracks)))
    if not tracks:
        raise psx_disc.NotADisc("a CHD with no track metadata, which DuckStation opens as no disc (:408-413)")
    return tracks


def _track_text(
    handle: BinaryIO, by_tag: dict[bytes, list[tuple[int, int]]], index: int
) -> tuple[bytes, re.Pattern[bytes]] | None:
    """The *index*-th ``CHT2`` entry's text, else the *index*-th ``CHTR``'s — a read that fails counts as none."""
    for tag, pattern in ((_CHT2, _CHT2_FORMAT), (_CHTR, _CHTR_FORMAT)):
        if index < len(by_tag[tag]):
            text = _metadata_text(handle, *by_tag[tag][index])
            if text is not None:
                return text, pattern
    return None


def _track(text: bytes, form: re.Pattern[bytes], index: int) -> _Track:
    """One track's metadata as DuckStation's sscanf and checks read it (cd_image_chd.cpp:282-366)."""
    match = form.match(text)
    if match is None:
        raise psx_disc.NotADisc(f"track metadata DuckStation's sscanf does not fill: {text[:80]!r}")
    fields = match.groups()
    pregap, pregap_type = (_s32(fields[4]), fields[5].decode("latin-1")) if form is _CHT2_FORMAT else (0, "")
    track = _Track(_s32(fields[0]), fields[1].decode("latin-1"), fields[2].decode("latin-1"), _s32(fields[3]),
                   pregap, pregap_type)
    if track.number != index + 1 or track.mode not in _TRACK_MODES:
        raise psx_disc.NotADisc(f"track {index + 1}'s metadata DuckStation refuses (cd_image_chd.cpp:320-333)")
    if track.pregap > 0 and track.pregap_type.startswith("V") and track.pregap > track.frames:
        raise psx_disc.NotADisc("a pregap longer than its track (cd_image_chd.cpp:361-366)")
    return track


def _track1(chd: _Chd) -> psx_disc.RawSectors:
    """Track 1's sectors, where the three emulators read them alike.

    Track 1 is read only as raw Mode 2 with no subcode type and no pregap kept
    in the file. Anything else the emulators read apart: Beetle opens a track
    of no other type and no other subtype (``CDAccess_CHD.cpp:107-116`` at
    d6383bf), and a pregap in the file puts DuckStation's index 1 after it
    (``cd_image_chd.cpp:341-370``) while Beetle reads absolute sector 4 from the
    file's start (``CDAccess_CHD.cpp:139-153``, :406). A hunk that is not whole
    frames is read apart too: DuckStation and SwanStation refuse it
    (``cd_image_chd.cpp:255-261``) where Beetle divides it into frames anyway
    (``CDAccess_CHD.cpp:407``).
    """
    header = chd.header
    if header.hunk_bytes % FRAME_SIZE:
        raise psx_disc.FormatNotRead(f"a hunk of {header.hunk_bytes} bytes, no whole number of CD frames")
    first = _tracks(chd.image.handle, header)[0]
    if first.mode == "AUDIO":
        raise psx_disc.NotADisc("track 1 is an audio track, so the disc has no data track to read a region from")
    if (
        first.mode != "MODE2_RAW"
        or not first.subtype.startswith("NONE")
        or (first.pregap > 0 and first.pregap_type.startswith("V"))
    ):
        raise psx_disc.FormatNotRead(
            "track 1 is not raw Mode 2 without subcode and without a pregap in the file, the one layout the three "
            "emulators read alike"
        )
    per_hunk = header.hunk_bytes // FRAME_SIZE

    def raw(lba: int) -> bytes | None:
        if lba >= first.frames:
            return None
        number, frame = divmod(lba, per_hunk)
        return chd.hunk(number)[frame * FRAME_SIZE : frame * FRAME_SIZE + SECTOR_SIZE]

    return raw


def read_identity(path: str, opener: psx_disc.Opener, lister: Lister) -> psx_disc.DiscIdentity:
    """The identity of the disc in the CHD at *path*, read through its parent where it names one.

    Raises :class:`ParentNotFound` where DuckStation's search finds no parent;
    :class:`~atlas.psx_disc.NotADisc`, :class:`~atlas.psx_disc.FormatNotRead`,
    :class:`~atlas.psx_disc.CodecUnavailable` and
    :class:`~atlas.psx_disc.Unreadable` as documented on them; and the
    opener's ``OSError`` for the file itself.
    """
    with ExitStack() as stack:
        handle = stack.enter_context(opener(path))
        header = _header(handle)
        if isinstance(header, _Older):
            raise psx_disc.FormatNotRead(f"a CHD of version {header.version}; atlas reads version 5 only")
        work = _Work()
        parent = None
        if header.requires_parent:
            directory = _directory(path)
            parent = _Folder(directory, opener, lister, stack, work).parent_of(header, 0)
            if parent is None:
                raise ParentNotFound(f"no file in {directory!r} opens as the parent this CHD names")
        return psx_disc.identity_of(_track1(_Chd(_Image(handle, header, work), parent, work)))

