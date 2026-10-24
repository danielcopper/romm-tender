"""Read a PlayStation disc stored as a PBP (a PSP ``EBOOT.PBP``) the way its emulators read it — stdlib only, zlib guarded.

A PBP made from a PlayStation disc holds the disc as 16-sector blocks of raw
2352-byte sectors, each block raw deflate or stored, found through a block
table behind an ISO header (``PSISOIMG0000``); a multi-disc PBP puts a title
header (``PSTITLEIMG…``) with a table of up to five such images in front. All
three emulators that decide a disc's region from its bytes open a PBP, with two
readers between them:

- **DuckStation** (``src/util/cd_image_pbp.cpp`` at
  RetroDECK/Duckstation@1f6de046b, the citations below without a name) and
  **SwanStation** (``src/common/cd_image_pbp.cpp`` at libretro/swanstation@4d309c0,
  the same logic through libretro's file stream, whose ``filestream_gets`` reads
  a line the way ``fgets`` does, ``file_stream.c:176-197``) check the header,
  parse ``PARAM.SFO`` and refuse a PBP that is not ``BOOTABLE`` or not of
  ``CATEGORY`` ``ME`` (:246-402), refuse an encrypted (``PGD``) image
  (:476-482, :538-551), and read the image's table of contents, its block base
  (``+0xBFC``) and each block's offset and size from its table entry
  (:553-588), track 1 starting where its table-of-contents entry puts it
  (:612-730).
- **Beetle PSX** (``mednafen/cdrom/CDAccess_PBP.cpp`` at
  libretro/beetle-psx-libretro@d6383bf, cited ``Beetle :line``) reads no
  ``PARAM.SFO``, compares a 12-byte title magic (:133-134), decrypts an
  encrypted image with Sony's keys and decodes its own codec (:143-157,
  :485-499, :403-407), takes the block base as fixed (``+0x100000``, :470) and
  each block's size from the next entry's offset (:617-630), and maps a sector
  number straight to its block, a converted image's data track forced to start
  at 0 (:545-550, :369-370). It ignores whether its own open succeeded
  (:221-230).

The two readers are read separately, and the disc's identity is read where
the readers that reach the disc read the same bytes: DuckStation's and
SwanStation's only where their ``PARAM.SFO`` and encryption checks pass,
Beetle's only where it does not decrypt. A disc on which the two readers that
reach it part — one finds no disc, or a sector read differs — is refused as a
format atlas does not read, the way :mod:`atlas.psx_disc` refuses a cue layout
they read apart. What each emulator's open decides before any disc is read
stands apart from the identity, in :class:`PbpFacts`.

Every rule reads the first disc of a multi-disc PBP, which is the one all three
open at launch (D1 of #661 and #691). Bounds: the ``PARAM.SFO`` entries a read
walks, the bytes its keys and values together ask to be read — of which only
``BOOTABLE``'s and ``CATEGORY``'s values are kept — and the blocks one identity
decodes; a read past one answers :class:`~atlas.psx_disc.Unreadable`.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import BinaryIO, Callable

from . import psx_disc

try:
    import zlib
except ImportError:  # a frozen runtime can ship without zlib
    zlib = None

PBP_SUFFIX = ".pbp"

SECTOR_SIZE = psx_disc.RAW_SECTOR_SIZE
# 16 raw sectors per block (cd_image_pbp.cpp:35; Beetle :369-370).
BLOCK_SIZE = 16 * SECTOR_SIZE
# The little-endian u32 both readers compare against "\0PGD".
_PGD = 0x44475000
_PGD_BYTES = b"\x00PGD"
_HEADER_SIZE = 0x28
_PSAR = 7
_SFO_HEADER_SIZE = 0x14
_SFO_ENTRY_SIZE = 0x10
_SFO_KEY_BUFFER = 20
_SFO_STRING = 0x0204
_SFO_U32 = 0x0404
_TITLE_MAGIC = b"PSTITLEIMG000000"
_BEETLE_TITLE_MAGIC = b"PSTITLEIMG00"
_IMAGE_MAGIC = b"PSISOIMG0000"
_DISC_TABLE = 5
_TOC_ENTRIES = 102
_TOC_ENTRY_SIZE = 10
_BLOCK_ENTRIES = 32256
_BLOCK_ENTRY_SIZE = 32
_BEETLE_HEADER_READ = 0xB6600
_BEETLE_BLOCK_ENTRIES = 22500
_BEETLE_DATA_BASE = 0x100000

# atlas's bounds, each far past a real PBP (PARAM.SFO holds about ten entries
# of a few dozen bytes): 4096 entries, 1 MiB asked of keys and values together
# — each key its fgets buffer, each string its stated size, each number four
# bytes — and 4096 blocks decoded in one identity, where a whole directory walk
# of 16 MiB is 512.
_MAX_SFO_ENTRIES = 4096
_MAX_SFO_BYTES = 1 << 20
_MAX_DECODES = 4096
# The two values IsValidEboot judges (cd_image_pbp.cpp:361-402); every other is
# read, so a refusal over it stands, and let go.
_EBOOT_KEYS = (b"BOOTABLE", b"CATEGORY")


class _Refused(Exception):
    """DuckStation's open refuses the file before reading any disc: its ``PARAM.SFO`` or its encryption."""


@dataclass(frozen=True, slots=True)
class PbpFacts:
    """What each emulator's own open decides about a PBP before it reads the disc.

    ``duckstation_refuses`` says whether DuckStation's and SwanStation's open
    refuses the file over its ``PARAM.SFO`` — a table that does not parse, a
    value of a type they do not read, ``BOOTABLE`` not 1 or ``CATEGORY`` not
    ``ME`` — or over an encrypted image (cd_image_pbp.cpp:361-402, :476-482,
    :538-551). ``encrypted`` says whether Beetle PSX finds an encrypted image,
    which it decrypts with Sony's keys and atlas does not read (Beetle
    :143-157, :485-499).
    """

    duckstation_refuses: bool
    encrypted: bool


def _u32(data: bytes, at: int) -> int:
    return int.from_bytes(data[at : at + 4], "little")


def _bcd(value: int) -> int:
    """``PackedBCDToBinary`` (common/bitutils.h:82-85) and Beetle's ``BCD_to_U8``, cut to a byte."""
    return ((value >> 4) * 10 + (value & 0x0F)) & 0xFF


def _msf(data: bytes) -> int:
    """Three BCD bytes as a frame count (``Position::FromBCD(...).ToLBA()``, cd_image.h:95-116)."""
    return (_bcd(data[0]) * 60 + _bcd(data[1])) * 75 + _bcd(data[2])


class _File:
    """One opened PBP and its length, read the way ``fread`` reads: all of a span, or nothing."""

    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle
        self.size = handle.seek(0, os.SEEK_END)

    def at(self, offset: int, size: int) -> bytes | None:
        """*size* bytes at *offset*, or ``None`` where the file ends first — a seek past the end succeeds and the
        read then fails, as the C's does."""
        if offset + size > self.size:
            return None
        self._handle.seek(offset)
        data = self._handle.read(size)
        return data if len(data) == size else None

    def fgets(self, offset: int, count: int) -> bytes | None:
        """``fgets`` into *count* bytes at *offset*, as a C string: ``None`` where it returns no line.

        At most ``count - 1`` bytes, up to and including a newline; nothing
        read before the end is a failure — so is a *count* below 1, which
        leaves no room — and *count* 1 reads nothing and succeeds (glibc's
        ``_IO_fgets``; ``filestream_gets`` the same for a count of 1 or more).
        The string ends at its first NUL.
        """
        if count == 1:
            return b""
        available = max(min(count - 1, self.size - offset), 0)
        data = self.at(offset, available) if available else b""
        if not data:
            return None
        newline = data.find(b"\n")
        if newline >= 0:
            data = data[: newline + 1]
        return data.split(b"\x00", 1)[0]


# ── DuckStation's and SwanStation's reader ────────────────────────────────


def _sfo_table(pbp: _File, sfo: int) -> dict[bytes, bytes | int]:
    """``LoadSFOHeader``, ``LoadSFOIndexTable`` and ``LoadSFOTable`` (cd_image_pbp.cpp:246-359).

    The header's magic is compared with ``strncmp`` against ``"\\0PSF"``,
    which stops at the literal's leading NUL, so only the first byte is
    tested. Every entry is read before any is judged; a string through
    ``fgets`` into a buffer of its stated size, a number as a u32. The first
    entry of a key wins (``std::map::emplace``), and only the two keys
    ``IsValidEboot`` judges are kept. Raises :class:`_Refused` where the
    reader refuses the table, :class:`~atlas.psx_disc.Unreadable` past
    :data:`_MAX_SFO_ENTRIES` or :data:`_MAX_SFO_BYTES`.
    """
    header = pbp.at(sfo, _SFO_HEADER_SIZE)
    if header is None or header[0] != 0:
        raise _Refused("no PARAM.SFO header where the PBP header points (cd_image_pbp.cpp:246-265)")
    key_table, data_table, count = _u32(header, 8), _u32(header, 12), _u32(header, 16)
    if count > _MAX_SFO_ENTRIES:
        raise psx_disc.Unreadable(f"a PARAM.SFO of {count} entries, past atlas's bound of {_MAX_SFO_ENTRIES}")
    index = pbp.at(sfo + _SFO_HEADER_SIZE, count * _SFO_ENTRY_SIZE)
    if index is None:
        raise _Refused("the PARAM.SFO index runs past the end of the file (cd_image_pbp.cpp:267-287)")
    table: dict[bytes, bytes | int] = {}
    asked = 0
    for number in range(count):
        entry = index[number * _SFO_ENTRY_SIZE : (number + 1) * _SFO_ENTRY_SIZE]
        key_offset, data_type = struct.unpack_from("<HH", entry)
        data_size, data_offset = _u32(entry, 4), _u32(entry, 12)
        asked += _SFO_KEY_BUFFER + (data_size if data_type == _SFO_STRING else 4)
        if asked > _MAX_SFO_BYTES:
            raise psx_disc.Unreadable(f"a PARAM.SFO asking more than atlas's bound of {_MAX_SFO_BYTES} bytes read")
        # Both sums are computed in u32 (:295-298).
        key = pbp.fgets((sfo + key_table + key_offset) & 0xFFFFFFFF, _SFO_KEY_BUFFER)
        if key is None:
            raise _Refused("a PARAM.SFO key that does not read (cd_image_pbp.cpp:306-312)")
        value = _sfo_value(pbp, (sfo + data_table + data_offset) & 0xFFFFFFFF, data_type, data_size)
        if key in _EBOOT_KEYS:
            table.setdefault(key, value)
    return table


def _sfo_value(pbp: _File, offset: int, data_type: int, size: int) -> bytes | int:
    """One PARAM.SFO value (cd_image_pbp.cpp:320-351): a string, a u32, or a refusal of any other type.

    A string's stated size is ``fgets``'s count, as an ``int``; a size of 0
    is refused, as glibc refuses the count (SwanStation's
    ``filestream_gets`` writes through the null pointer an empty buffer hands
    it, which opens nothing either).
    """
    if data_type == _SFO_STRING:
        value = pbp.fgets(offset, size)
        if value is None:
            raise _Refused("a PARAM.SFO string that does not read (cd_image_pbp.cpp:325-335)")
        return value
    if data_type == _SFO_U32:
        raw = pbp.at(offset, 4)
        if raw is None:
            raise _Refused("a PARAM.SFO number that does not read (cd_image_pbp.cpp:336-346)")
        return _u32(raw, 0)
    raise _Refused(f"a PARAM.SFO value of type {data_type:#06x}, which the reader refuses (cd_image_pbp.cpp:320-351)")


def _check_eboot(table: dict[bytes, bytes | int]) -> None:
    """``IsValidEboot`` (cd_image_pbp.cpp:361-402): ``BOOTABLE`` the number 1, ``CATEGORY`` the string ``ME``."""
    bootable, category = table.get(b"BOOTABLE"), table.get(b"CATEGORY")
    if isinstance(bootable, bytes) or bootable != 1:
        raise _Refused("PARAM.SFO's BOOTABLE is not the number 1 (cd_image_pbp.cpp:365-381)")
    if category != b"ME":
        raise _Refused('PARAM.SFO\'s CATEGORY is not the string "ME" (cd_image_pbp.cpp:383-399)')


@dataclass(frozen=True, slots=True)
class _Track1:
    """Where a reader finds track 1: the image sector its index 1 starts at, and how many sectors it holds."""

    first: int
    length: int


def _duckstation_image(pbp: _File, header: bytes) -> int:
    """``Open`` past the PARAM.SFO (cd_image_pbp.cpp:454-505): the first disc's ISO header offset.

    The title magic is compared over 16 bytes; a multi-disc table whose first
    entry reads as ``"\\0PGD"`` is encrypted. Offsets are u32 sums.
    """
    psar = _u32(header, 8 + 4 * _PSAR)
    magic = pbp.at(psar, len(_TITLE_MAGIC))
    if magic is None:
        raise psx_disc.NotADisc("the PBP's data section lies past the end of the file (cd_image_pbp.cpp:455-461)")
    if magic != _TITLE_MAGIC:
        return psar
    table = pbp.at((psar + 0x200) & 0xFFFFFFFF, 4 * _DISC_TABLE)
    if table is None:
        raise psx_disc.NotADisc("the multi-disc table lies past the end of the file (cd_image_pbp.cpp:469-474)")
    if _u32(table, 0) == _PGD:
        raise _Refused("an encrypted multi-disc table (cd_image_pbp.cpp:476-482)")
    if _u32(table, 0) == 0:
        raise psx_disc.NotADisc("a multi-disc PBP that names no disc (cd_image_pbp.cpp:484-497)")
    return (psar + _u32(table, 0)) & 0xFFFFFFFF


def _duckstation_toc(toc: bytes) -> _Track1:
    """The table-of-contents walk of ``OpenDisc`` (cd_image_pbp.cpp:593-730), every track checked, track 1 kept.

    Track 1's index 1 starts in the image at its ``userdata_start`` less the
    pregap the reader puts before it — ``pregap_start``, or 0 where the user
    data starts before the pregap (:637-668, :687-689) — and its length runs to
    the next track's pregap, or for a last track to the sector count plus its
    pregap (:700-723). Arithmetic is in u32, as ``LBA`` is.
    """
    entries = [toc[n * _TOC_ENTRY_SIZE : (n + 1) * _TOC_ENTRY_SIZE] for n in range(_TOC_ENTRIES)]
    if (entries[0][2], entries[1][2], entries[2][2]) != (0xA0, 0xA1, 0xA2):
        raise psx_disc.NotADisc("the TOC's first three points are not A0, A1, A2 (cd_image_pbp.cpp:595-599)")
    first_track, last_track = _bcd(entries[0][7]), _bcd(entries[1][7])
    if first_track != 1 or last_track < first_track:
        raise psx_disc.NotADisc("the TOC's first track is not 1, or its last precedes it (cd_image_pbp.cpp:606-610)")
    if last_track + 3 > _TOC_ENTRIES:
        raise psx_disc.Unreadable(
            f"a TOC naming {last_track} tracks, whose entries the reader reads past its 102 (cd_image_pbp.cpp:620-623)"
        )
    first_data, pregap1 = _duckstation_pregap(entries[3], 1)
    # The sector count grows by track 1's pregap (:664-668).
    lba_count = (_msf(entries[2][7:10]) + pregap1) & 0xFFFFFFFF
    lengths = [
        _duckstation_length(entries, number, last_track, lba_count, _duckstation_pregap(entries[number + 2], number)[0])
        for number in range(1, last_track + 1)
    ]
    if entries[3][0] == 0x01:
        raise psx_disc.NotADisc("track 1 is an audio track, so the disc has no data track to read a region from")
    return _Track1((first_data - pregap1) & 0xFFFFFFFF, lengths[0])


def _duckstation_pregap(entry: bytes, number: int) -> tuple[int, int]:
    """A track's ``userdata_start`` and the pregap before it (cd_image_pbp.cpp:637-662).

    Where the user data starts before the pregap, the pregap is taken as not
    in the file — for track 1 of data only; any other track is refused.
    """
    pregap_start, userdata_start = _msf(entry[3:6]), _msf(entry[7:10])
    if userdata_start >= pregap_start:
        return userdata_start, userdata_start - pregap_start
    if number != 1 or entry[0] == 0x01:
        raise psx_disc.NotADisc(f"track {number}'s data starts before its pregap (cd_image_pbp.cpp:642-649)")
    return userdata_start, userdata_start


def _duckstation_length(entries: list[bytes], number: int, last_track: int, lba_count: int, start: int) -> int:
    """A track's user-data length: to the next track's pregap, or for the last to the disc's end (:700-723)."""
    if number == last_track:
        if start >= lba_count:
            raise psx_disc.NotADisc("the last track starts past the disc's end (cd_image_pbp.cpp:700-706)")
        return lba_count - start
    following = entries[number + 3]
    next_start = _msf(following[3:6])
    if _bcd(following[2]) != number + 1 or next_start < start:
        raise psx_disc.NotADisc(f"track {number}'s length cannot be taken (cd_image_pbp.cpp:709-723)")
    return next_start - start


@dataclass(frozen=True, slots=True)
class _Block:
    offset: int
    size: int


class _Work:
    """The blocks one identity read decodes, both readers together; past :data:`_MAX_DECODES` it is unreadable."""

    def __init__(self) -> None:
        self._decodes = 0

    def spend(self) -> None:
        self._decodes += 1
        if self._decodes > _MAX_DECODES:
            raise psx_disc.Unreadable(f"an identity read decoding more than atlas's bound of {_MAX_DECODES} blocks")


class _Blocks:
    """A reader's blocks of one image: decoded on demand, the last one kept, every decode charged."""

    def __init__(self, pbp: _File, work: _Work) -> None:
        self._pbp = pbp
        self._work = work
        self._kept: tuple[int, bytes] | None = None

    def decoded(self, number: int, block: _Block, *, exact: bool) -> bytes:
        """Block *number*'s bytes — stored where its size is a whole block, raw deflate otherwise.

        *exact* is Beetle's demand that a stream fill the block exactly
        (Beetle :418-422); DuckStation's accepts a stream that ends short and
        reads what its buffer held before past the end, which comes back here
        as the shorter bytes.
        """
        if self._kept is not None and self._kept[0] == number:
            return self._kept[1]
        self._work.spend()
        source = self._pbp.at(block.offset, block.size)
        if source is None:
            raise psx_disc.Unreadable(f"block {number} lies past the end of the file")
        data = source if block.size == BLOCK_SIZE else _inflate(source, number)
        if exact and len(data) != BLOCK_SIZE:
            raise psx_disc.Unreadable(f"block {number} decodes to {len(data)} bytes, not a whole block")
        self._kept = (number, data)
        return data


def _inflate(data: bytes, number: int) -> bytes:
    """Raw deflate into one block's room, finished (``inflate(Z_FINISH)`` must reach the stream's end)."""
    if zlib is None:
        raise psx_disc.CodecUnavailable("a PBP's blocks need the zlib module, which this runtime does not have")
    stream = zlib.decompressobj(-15)
    try:
        out = stream.decompress(data, BLOCK_SIZE)
    except zlib.error as error:
        raise psx_disc.Unreadable(f"block {number} is a deflate stream that does not decode") from error
    if not stream.eof:
        raise psx_disc.Unreadable(f"block {number} is a deflate stream that does not end within one block")
    return out


def _duckstation_open(pbp: _File) -> tuple[list[_Block], _Track1]:
    """DuckStation's open of the first disc (cd_image_pbp.cpp:404-757): its blocks and where track 1 lies.

    Raises :class:`_Refused` over the PARAM.SFO and the encryption,
    :class:`~atlas.psx_disc.NotADisc` over the disc's own structure.
    """
    header = pbp.at(0, _HEADER_SIZE)
    # strncmp against "\0PBP" stops at the literal's NUL (:233-237).
    if header is None or header[0] != 0:
        raise psx_disc.NotADisc("no PBP header (cd_image_pbp.cpp:213-244)")
    _check_eboot(_sfo_table(pbp, _u32(header, 8)))
    iso = _duckstation_image(pbp, header)
    if pbp.at(iso, len(_IMAGE_MAGIC)) != _IMAGE_MAGIC:
        raise psx_disc.NotADisc("no PSISOIMG0000 header where the first disc should start (cd_image_pbp.cpp:523-536)")
    pgd = pbp.at((iso + 0x400) & 0xFFFFFFFF, 4)
    if pgd is None:
        raise psx_disc.NotADisc("the ISO header is cut short (cd_image_pbp.cpp:538-544)")
    if _u32(pgd, 0) == _PGD:
        raise _Refused("an encrypted image (cd_image_pbp.cpp:546-551)")
    toc = pbp.at((iso + 0x800) & 0xFFFFFFFF, _TOC_ENTRIES * _TOC_ENTRY_SIZE)
    base = pbp.at((iso + 0xBFC) & 0xFFFFFFFF, 4)
    table = pbp.at((iso + 0x4000) & 0xFFFFFFFF, _BLOCK_ENTRIES * _BLOCK_ENTRY_SIZE)
    if toc is None or base is None or table is None:
        raise psx_disc.NotADisc("the ISO header's TOC or block table runs past the end of the file (:553-588)")
    blocks = []
    for number in range(_BLOCK_ENTRIES):
        offset, size = struct.unpack_from("<IH", table, number * _BLOCK_ENTRY_SIZE)
        blocks.append(_Block((iso + _u32(base, 0) + offset) & 0xFFFFFFFF if size else 0, size))
    return blocks, _duckstation_toc(toc)


def _duckstation_sectors(pbp: _File, work: _Work) -> psx_disc.RawSectors:
    """DuckStation's sector read of track 1 (cd_image_pbp.cpp:785-855), once :func:`_duckstation_open` has opened it."""
    blocks, track1 = _duckstation_open(pbp)
    reader = _Blocks(pbp, work)

    def raw(lba: int) -> bytes | None:
        if lba >= track1.length:
            return None
        # ReadSectorFromIndex (:833-855), its offset a u32.
        offset = (track1.first * SECTOR_SIZE + lba * SECTOR_SIZE) & 0xFFFFFFFF
        number, within = divmod(offset, BLOCK_SIZE)
        if number >= _BLOCK_ENTRIES:
            raise psx_disc.Unreadable(f"sector {lba} lies in block {number}, past the reader's table of {_BLOCK_ENTRIES}")
        block = blocks[number]
        if block.size == 0:
            raise psx_disc.Unreadable(f"sector {lba} lies in block {number}, which the table marks empty (:841-845)")
        data = reader.decoded(number, block, exact=False)
        if within + SECTOR_SIZE > len(data):
            raise psx_disc.Unreadable(
                f"sector {lba} lies past where block {number}'s stream ends, in bytes the reader's buffer kept"
            )
        return data[within : within + SECTOR_SIZE]

    return raw


# ── Beetle PSX's reader ───────────────────────────────────────────────────


class _Encrypted(Exception):
    """Beetle finds an encrypted image, which it decrypts with Sony's keys and atlas does not read."""


def _beetle_at(pbp: _File, offset: int, size: int, what: str) -> bytes:
    """A read Beetle makes without checking it (``Stream::read``): a short one leaves bytes atlas cannot know."""
    data = pbp.at(offset & 0xFFFFFFFF, size)
    if data is None:
        raise psx_disc.Unreadable(f"{what} runs past the end of the file, and Beetle reads on with what it holds")
    return data


def _beetle_image(pbp: _File) -> int:
    """``ImageOpen`` (Beetle :103-208): the image offset ``Read_TOC`` reads from, whatever the open returned.

    The constructor ignores the open's result (:221-230), so a failure past
    the point the offset is set reads on from there; one before it — a header
    whose magic is not exactly ``"\\0PBP"`` — leaves the offset unset.
    """
    magic = pbp.at(0, 4)
    if magic != b"\x00PBP":
        raise psx_disc.NotADisc("the header's magic is not \\0PBP, so Beetle's open sets no image offset (:118-122)")
    psar = _u32(_beetle_at(pbp, 8, 4 * 8, "the PBP header"), 4 * _PSAR)
    if _beetle_at(pbp, psar, len(_BEETLE_TITLE_MAGIC), "the data section's magic") != _BEETLE_TITLE_MAGIC:
        return psar
    iso_map = _beetle_at(pbp, psar + 0x200, 0x2A0, "the multi-disc table")
    if iso_map[:4] == _PGD_BYTES:
        raise _Encrypted("an encrypted multi-disc table (Beetle :143-157)")
    first = int.from_bytes(iso_map[:4], "little", signed=True)
    # A table naming no disc fails the open with the offset left at the data
    # section, which Read_TOC then reads (:159-174); otherwise the first disc's (:176-180).
    return psar if first == 0 else (psar + first) & 0xFFFFFFFF


def _beetle_sectors(pbp: _File, work: _Work) -> psx_disc.RawSectors:
    """Beetle's ``Read_TOC`` (Beetle :445-663) and ``Read_Raw_Sector`` (:365-443) for the first disc.

    The image is read whatever its ``PSISOIMG0000`` magic says (:183-187
    returns before ``Read_TOC`` but its result is ignored). The table of
    contents names the track count and the disc's length; a converted image's
    data track is forced to start at sector 0 (:545-550). The block table is
    read to its first empty entry, each block's size the distance to the next
    entry's offset (:617-630). A sector reads block ``lba >> 4``, past the
    lead-out not at all (``cdromif.cpp:450-451``).
    """
    image = _beetle_image(pbp)
    header = _beetle_at(pbp, image + 0x400, _BEETLE_HEADER_READ, "the image header Beetle reads")
    if header[:4] == _PGD_BYTES:
        raise _Encrypted("an encrypted image (Beetle :485-499)")
    toc = 0x400
    count = _bcd(header[toc + _TOC_ENTRY_SIZE + 7])
    total = _msf(header[toc + 2 * _TOC_ENTRY_SIZE + 7 : toc + 2 * _TOC_ENTRY_SIZE + 10])
    if not 1 <= count <= 99:
        raise psx_disc.NotADisc(
            f"Beetle's TOC names {count} tracks, a first or last track its CD interface refuses (cdromif.cpp:600-603)"
        )
    entries = [header[toc + (n + 2) * _TOC_ENTRY_SIZE : toc + (n + 3) * _TOC_ENTRY_SIZE] for n in range(1, count + 1)]
    for number, entry in enumerate(entries, 1):
        if _bcd(entry[2]) != number:
            raise psx_disc.NotADisc("Beetle's TOC lists its tracks out of order (Beetle :578-582)")
    if entries[0][0] == 1:
        raise psx_disc.NotADisc("track 1 is an audio track, so the disc has no data track to read a region from")
    length = _beetle_track1_length(entries, total)
    offsets = _beetle_offsets(header, image)
    reader = _Blocks(pbp, work)

    def raw(lba: int) -> bytes | None:
        if lba >= min(length, total):
            return None
        number = lba >> 4
        if number + 1 >= len(offsets):
            raise psx_disc.Unreadable(f"sector {lba} lies in block {number}, past what Beetle's block table fills")
        start, size = offsets[number], (offsets[number + 1] - offsets[number]) & 0xFFFFFFFF
        if size > BLOCK_SIZE:
            raise psx_disc.Unreadable(f"block {number} is larger than a block, which Beetle refuses (Beetle :388-392)")
        data = reader.decoded(number, _Block(start, size), exact=True)
        within = (lba & 0x0F) * SECTOR_SIZE
        return data[within : within + SECTOR_SIZE]

    return raw


def _beetle_track1_length(entries: list[bytes], total: int) -> int:
    """Track 1's sectors as Beetle counts them (Beetle :562-570): to the next track's index 0, or the disc's end.

    Track 1 starts at 0, as every data track of a converted image is forced
    to; so is a second data track, while an audio track keeps the index 0 its
    entry states.
    """
    if len(entries) == 1:
        return total
    second = entries[1]
    return _msf(second[3:6]) if second[0] == 1 else 0


def _beetle_offsets(header: bytes, image: int) -> list[int]:
    """Beetle's block table (Beetle :617-630): offsets from the fixed base, up to and including the closing one.

    The table is read until an entry of size 0; the offset after the last
    block is that entry's own offset, or, with no such entry, the last entry's
    end. Entries past it are memory Beetle never filled, so a block that would
    need one is not read.
    """
    base = (image + _BEETLE_DATA_BASE) & 0xFFFFFFFF
    at = 0x3C00
    offsets: list[int] = []
    offset = size = 0
    for _ in range(_BEETLE_BLOCK_ENTRIES):
        offset, size = struct.unpack_from("<IH", header, at)
        at += _BLOCK_ENTRY_SIZE
        if size == 0:
            break
        offsets.append((base + offset) & 0xFFFFFFFF)
    offsets.append((base + offset + size) & 0xFFFFFFFF)
    return offsets


# ── The two together ──────────────────────────────────────────────────────


def _reader(build: Callable[[], psx_disc.RawSectors]) -> psx_disc.RawSectors | Exception:
    """A reader's sectors, or what its open raised — kept, because the other reader may still reach the disc."""
    try:
        return build()
    except (_Refused, _Encrypted, psx_disc.NotADisc, psx_disc.Unreadable) as error:
        return error


def _read(pbp: _File) -> tuple[PbpFacts, psx_disc.RawSectors | Exception]:
    """Both readers' opens: the facts, and the sectors the readers that reach the disc read alike — or why none.

    A bound atlas hit while opening is :class:`~atlas.psx_disc.Unreadable`
    whatever the other reader found; one reader finding no disc where the
    other finds one is the two reading the file apart.
    """
    work = _Work()
    duckstation = _reader(lambda: _duckstation_sectors(pbp, work))
    beetle = _reader(lambda: _beetle_sectors(pbp, work))
    facts = PbpFacts(isinstance(duckstation, _Refused), isinstance(beetle, _Encrypted))
    reaching = [r for r in (duckstation, beetle) if not isinstance(r, (_Refused, _Encrypted))]
    if not reaching:
        return facts, psx_disc.FormatNotRead("no reader that opens this PBP reaches its disc")
    failed = [r for r in reaching if isinstance(r, Exception)]
    sectors = [r for r in reaching if not isinstance(r, Exception)]
    bound = next((r for r in failed if isinstance(r, psx_disc.Unreadable)), None)
    if bound is not None:
        return facts, bound
    if failed and sectors:
        return facts, psx_disc.FormatNotRead(
            "DuckStation's and Beetle's readers part over this PBP: one finds a disc where the other finds none"
        )
    if failed:
        return facts, failed[0]
    return facts, sectors[0] if len(sectors) == 1 else _agreeing(sectors[0], sectors[1])


def _agreeing(first: psx_disc.RawSectors, second: psx_disc.RawSectors) -> psx_disc.RawSectors:
    """The sectors two readers read alike: a sector they read apart refuses the disc, one either cannot read is
    unreadable."""

    def raw(lba: int) -> bytes | None:
        outcomes: list[bytes | None | Exception] = []
        for reader in (first, second):
            try:
                outcomes.append(reader(lba))
            except psx_disc.Unreadable as error:
                outcomes.append(error)
        a, b = outcomes
        # A read one reader could not finish leaves its bytes unknown.
        if isinstance(a, Exception):
            raise a
        if isinstance(b, Exception):
            raise b
        if a != b:
            raise psx_disc.FormatNotRead(
                f"DuckStation's and Beetle's readers read sector {lba} of this PBP apart, the one layout question "
                "the three emulators would answer differently"
            )
        return a

    return raw


@dataclass(frozen=True, slots=True)
class PbpRead:
    """One read of a PBP: what both opens decided, and the identity — or what reading it raised.

    ``refusal`` is set exactly when ``identity`` is not: the
    :class:`~atlas.psx_disc.FormatNotRead`, :class:`~atlas.psx_disc.NotADisc`,
    :class:`~atlas.psx_disc.CodecUnavailable` or
    :class:`~atlas.psx_disc.Unreadable` documented on :func:`read_identity`.
    """

    facts: PbpFacts
    identity: psx_disc.DiscIdentity | None
    refusal: Exception | None


def read(path: str, opener: psx_disc.Opener) -> PbpRead:
    """The PBP at *path* read once: both opens' facts, then the identity; the opener's ``OSError`` propagates."""
    with opener(path) as handle:
        facts, sectors = _read(_File(handle))
        if isinstance(sectors, Exception):
            return PbpRead(facts, None, sectors)
        try:
            return PbpRead(facts, psx_disc.identity_of(sectors), None)
        except (psx_disc.FormatNotRead, psx_disc.NotADisc, psx_disc.CodecUnavailable, psx_disc.Unreadable) as error:
            return PbpRead(facts, None, error)


def read_facts(path: str, opener: psx_disc.Opener) -> PbpFacts:
    """What each emulator's open decides about the PBP at *path*; the opener's ``OSError`` propagates."""
    return read(path, opener).facts


def read_identity(path: str, opener: psx_disc.Opener) -> psx_disc.DiscIdentity:
    """The identity of the first disc in the PBP at *path*, read where the readers that reach it read alike.

    Raises :class:`~atlas.psx_disc.FormatNotRead` where no reader reaches the
    disc or two read it apart, :class:`~atlas.psx_disc.NotADisc` where the
    readers that reach it find no disc, :class:`~atlas.psx_disc.CodecUnavailable`
    and :class:`~atlas.psx_disc.Unreadable` as documented on them, and the
    opener's ``OSError``.
    """
    result = read(path, opener)
    if result.refusal is not None:
        raise result.refusal
    assert result.identity is not None
    return result.identity
