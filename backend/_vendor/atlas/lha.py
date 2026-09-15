"""Read an LhA archive — the member list, and one member's bytes — stdlib only.

PUAE hands an ``.lha`` straight to UAE, which mounts it as a read-only
filesystem and reads it with its own LhA archiver (``sources/src/zfile.c``
:1483-1484 dispatching to ``ArchiveFormatLHA``, ``zfile_archive.c``
:2187-2188, both at libretro/libretro-uae 0043cf9). The Amiga then sees the
archive's own directory tree, so what a save answer needs out of such a file —
which members are in it, and the bytes of the one the boot script would run —
is a read of the archive itself. Zero runtime dependencies is a contract, so
this module is that reader: no ``lha`` binary, no ``libarchive``.

**The container.** The header layout is the one distributed with LHa for UNIX
as ``header.doc`` (github.com/jca02266/lha), sections A and B: three header
levels, told apart by the byte at offset 20.

- level 0 — ``header size`` at offset 0 counts the bytes from offset 2 to the
  end of the header, so the header is ``2 + size`` bytes and the member's
  compressed data follows it. The whole path lives in the base header.
- level 1 — the same base header, but offset 7 holds a *skip size* covering
  the compressed data **and** the extended headers that follow the base one,
  so the next member's header is at ``pos + 2 + size + skip``. The base header
  carries the file name and the extended headers carry the directory
  (type ``0x02``, components ended by ``0xFF``).
- level 2 — offset 0 holds a 16-bit *total* header size covering the base
  header and every extended header (padding included), the name comes out of
  the extended headers alone, and the data follows at ``pos + total``.

An extended header is ``1 type + payload + 2 next-header size``, and the
chain ends on a next-header size of zero; the base header's last two bytes
are the first such size. The archive ends where a header would start with a
zero size *and* a zero checksum, or simply at the end of the file — both are
in the wild, and the two-byte test is what keeps a level-2 header whose size
is a multiple of 256 from being read as the end.

Two guards bound the decode, and between them the work is bounded. The loop
stops as soon as the bit reader is spent, so a header whose stated size does
not match the bytes behind it ends in :class:`CorruptMember` rather than in
an endless run of invented literals; and no member is decoded past
:data:`_MAX_MEMBER_BYTES`, because a legal stream of degenerate blocks can
expand a few thousand compressed bytes into tens of megabytes before the
first guard has anything to say.

**The compression.** Only two methods are read here. ``-lh0-`` is the member
stored verbatim. ``-lh5-`` is LZSS over an 8 KiB window with static Huffman
codes, in the shape LHa for UNIX implements it: a stream of blocks, each
opening with a 16-bit symbol count, then three code tables — the 19-symbol
table that codes the *lengths* of the literal table, the 510-symbol literal
and match-length table, and the 14-symbol position table — and then that many
symbols, where a symbol below 256 is a byte and one at or above it is a match
whose length is ``symbol - 256 + 3`` and whose distance the position table
gives. Every other method (``-lh1-``, ``-lh6-``, ``-lh7-``, ``-lzs-``, …)
raises :class:`UnsupportedMethod` naming itself rather than being guessed at.

The reader is checked two ways beyond its unit tests, because an
implementation of a format this old is worth more evidence than a
description: every member of a real Amiga WHDLoad install archive decodes to
bytes identical to ``7z``'s extraction of the same member and to the CRC-16
its own header states, and the fixture under ``tests/data`` lists and tests
clean under Lhasa (an independent LhA implementation). The CRC is the
in-band check that ships: :func:`extract` verifies it and refuses a member
whose decoded bytes do not match, so a decoder defect cannot reach a caller
as content.

Names are decoded as ``latin-1`` — one byte, one character, which is what an
Amiga wrote — and both separators an LhA header can carry (``0xFF`` and
``\\``) become ``/``, so a member name is one spelling wherever it is read.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# Header field offsets shared by levels 0 and 1, and the one that tells every
# level apart.
_I_SIZE = 0
_I_CHECKSUM = 1
_I_METHOD = 2
_I_PACKED = 7
_I_ORIGINAL = 11
_I_LEVEL = 20
_I_NAME_LENGTH = 21
_I_NAME = 22
_METHOD_LENGTH = 5
# The shortest prefix every level shares: enough to read the level byte and,
# for levels 0 and 1, the name length.
_COMMON_HEADER = 22
# Level 2 has no name in its base header; its fixed part is the 26 bytes
# header.doc counts, ending with the first extended header's size.
_LEVEL2_HEADER = 26
_I_LEVEL2_CRC = 21
_I_LEVEL2_NEXT = 24

_EXT_FILENAME = 0x01
_EXT_DIRNAME = 0x02
# An extended header is at least its type byte plus its own trailing size.
_EXT_MINIMUM = 3
_EXT_SIZE_FIELD = 2

STORED = b"-lh0-"
LH5 = b"-lh5-"

_AMIGA_SEPARATOR = "\xff"
_DOS_SEPARATOR = "\\"
_SEPARATOR = "/"

# -lh5- constants, all from the format itself: 256 literal symbols, the
# shortest match it can code, the two Huffman alphabets and the widths their
# counts are sent in.
_LITERALS = 256
_THRESHOLD = 3
_CODES = 510
_CODE_COUNT_BITS = 9
_CODE_TABLE_BITS = 12
_PRE_CODES = 19
_PRE_COUNT_BITS = 5
_PRE_TABLE_BITS = 8
# After the third pre-code length a two-bit run says how many entries to skip
# — the one irregularity in that table's transmission.
_PRE_SKIP_AFTER = 3
# A pre-code length of 7 is an escape: three ones, then one more one per step
# above 7, then a zero.
_PRE_ESCAPE = 7
_POSITIONS = 14
_POSITION_COUNT_BITS = 4
# Huffman code lengths are transmitted in a 16-bit code space, so nothing
# longer than that can be part of a legal table.
_MAX_CODE_LENGTH = 16
_CODE_SPACE = 1 << _MAX_CODE_LENGTH
_BLOCK_COUNT_BITS = 16
# The two shorthand codes the literal-length table uses for runs of zeros,
# and what each adds to the count that follows it.
_RUN_SHORT_BITS = 4
_RUN_SHORT_BASE = 3
_RUN_LONG_BASE = 20

# How far past the compressed bytes the reader may legitimately run: a block's
# last symbol can need bits the encoder never wrote, because the symbol count
# and not the byte count is what ends a block. Two 16-bit peeks' worth is more
# than any one symbol can ask for, and past it the reader is decoding zeros —
# which is what turns a forged original size into an endless run of literals.
_END_MARGIN_BITS = 32
# The most any member this package decodes can weigh. A slave is a few
# kilobytes and a `custom` file is bytes, so nothing legitimate comes near it
# — and without a ceiling a legal stream of degenerate blocks (a symbol count
# of 65535 against a table holding one zero-length symbol) turns a few
# thousand compressed bytes into tens of megabytes before the end of the
# stream is reached.
_MAX_MEMBER_BYTES = 4 << 20

_CRC16_POLYNOMIAL = 0xA001


def _crc16_table() -> tuple[int, ...]:
    """The CRC-16/ARC table LhA checks a member's decoded bytes against."""
    table = []
    for byte in range(256):
        value = byte
        for _ in range(8):
            value = (value >> 1) ^ _CRC16_POLYNOMIAL if value & 1 else value >> 1
        table.append(value)
    return tuple(table)


_CRC16 = _crc16_table()


def crc16(data: bytes) -> int:
    """The checksum an LhA header states over a member's uncompressed bytes."""
    value = 0
    for byte in data:
        value = (value >> 8) ^ _CRC16[(value ^ byte) & 0xFF]
    return value


class LhaError(Exception):
    """Anything that stops this reader — the three subclasses say which."""


class NotAnLha(LhaError):
    """The bytes do not walk as an LhA archive: no header where one must be."""


class UnsupportedMethod(LhaError):
    """A member is compressed by a method this reader does not implement."""

    def __init__(self, method: str) -> None:
        super().__init__(f"member method {method!r} is not one this reader implements")
        self.method = method


class CorruptMember(LhaError):
    """A member walked fine and its bytes do not come back — truncated, or a failed CRC."""


@dataclass(frozen=True, slots=True)
class Member:
    """One member of an archive: where its bytes are, and how to read them back.

    ``name`` is the member's path inside the archive with ``/`` separators.
    ``offset`` and ``packed_size`` bound the compressed bytes in the archive,
    ``original_size`` is what they decompress to, and ``crc`` is the checksum
    the header states over the decompressed bytes — ``None`` for a level-0
    header too short to carry one, where there is nothing to check against.
    """

    name: str
    method: bytes
    offset: int
    packed_size: int
    original_size: int
    crc: int | None


def members(data: bytes) -> tuple[Member, ...]:
    """Every member of the archive, in the order its headers appear.

    Raises :class:`NotAnLha` where the walk finds no header at the start or
    meets bytes that do not continue it — a damaged archive and a file that
    was never one reach a caller as the same thing, that this reader cannot
    list it.
    """
    walked: list[Member] = []
    position = 0
    while not _archive_ends_at(data, position):
        member, position = _read_header(data, position)
        walked.append(member)
    if not walked:
        raise NotAnLha("no member header at offset 0 — an LhA archive opens with one")
    return tuple(walked)


def extract(data: bytes, member: Member) -> bytes:
    """One member's bytes, decompressed and checked against its own CRC.

    Only the member asked for is touched: an archive is never decompressed
    whole to reach one file inside it.
    """
    packed = data[member.offset : member.offset + member.packed_size]
    if len(packed) != member.packed_size:
        raise CorruptMember(f"member {member.name!r} is cut short by the end of the archive")
    content = _decompress(packed, member)
    if len(content) != member.original_size:
        raise CorruptMember(
            f"member {member.name!r} decoded to {len(content)} bytes, not the "
            f"{member.original_size} its header states"
        )
    if member.crc is not None and crc16(content) != member.crc:
        raise CorruptMember(f"member {member.name!r} fails the CRC-16 its header states")
    return content


def _decompress(packed: bytes, member: Member) -> bytes:
    if member.method == STORED:
        return packed
    if member.method == LH5:
        return _inflate_lh5(packed, member.original_size)
    raise UnsupportedMethod(member.method.decode("latin-1"))


# ---------------------------------------------------------------------------
# The container: the header walk.
# ---------------------------------------------------------------------------


def _archive_ends_at(data: bytes, position: int) -> bool:
    """Is the walk over — out of bytes, or standing on the end marker?

    The marker is a zero size *and* a zero checksum, because a level-2 header
    whose total size is a multiple of 256 has a zero low byte and is a header
    all the same.
    """
    if position + _EXT_SIZE_FIELD > len(data):
        return True
    return data[position + _I_SIZE] == 0 and data[position + _I_CHECKSUM] == 0


def _read_header(data: bytes, position: int) -> tuple[Member, int]:
    """One member header, and where the next one starts."""
    if position + _COMMON_HEADER > len(data):
        raise NotAnLha(f"a member header at offset {position} is cut short by the end of the file")
    level = data[position + _I_LEVEL]
    if level == 2:
        return _read_level2(data, position)
    if level in (0, 1):
        return _read_level01(data, position, level)
    raise NotAnLha(f"header level {level} at offset {position} is not one this reader knows")


def _read_level01(data: bytes, position: int, level: int) -> tuple[Member, int]:
    """A level-0 or level-1 header — one base header, two ways to continue."""
    base_end = position + _EXT_SIZE_FIELD + data[position + _I_SIZE]
    if base_end > len(data):
        raise NotAnLha(f"the header at offset {position} runs past the end of the file")
    stated_size = _u32(data, position + _I_PACKED)
    original_size = _u32(data, position + _I_ORIGINAL)
    name_length = data[position + _I_NAME_LENGTH]
    name = data[position + _I_NAME : position + _I_NAME + name_length]
    crc = _optional_u16(data, position + _I_NAME + name_length, base_end)
    if level == 0:
        return (
            _member(_member_name(b"", name), data, position, base_end, stated_size, original_size, crc),
            base_end + stated_size,
        )
    directory, override, data_start = _read_extended(data, base_end, _u16(data, base_end - _EXT_SIZE_FIELD))
    packed_size = stated_size - (data_start - base_end)
    if packed_size < 0:
        raise NotAnLha(f"the extended headers at offset {base_end} overrun the member's skip size")
    return (
        _member(
            _member_name(directory, override if override is not None else name),
            data,
            position,
            data_start,
            packed_size,
            original_size,
            crc,
        ),
        base_end + stated_size,
    )


def _read_level2(data: bytes, position: int) -> tuple[Member, int]:
    """A level-2 header — the name lives in the extended headers alone."""
    total = _u16(data, position + _I_SIZE)
    if total < _LEVEL2_HEADER or position + total > len(data):
        raise NotAnLha(f"the level-2 header at offset {position} states an impossible size")
    packed_size = _u32(data, position + _I_PACKED)
    original_size = _u32(data, position + _I_ORIGINAL)
    crc = _u16(data, position + _I_LEVEL2_CRC)
    first = _u16(data, position + _I_LEVEL2_NEXT)
    directory, name, _ = _read_extended(data, position + _LEVEL2_HEADER, first)
    data_start = position + total
    return (
        _member(_member_name(directory, name or b""), data, position, data_start, packed_size, original_size, crc),
        data_start + packed_size,
    )


def _member(
    name: str,
    data: bytes,
    position: int,
    offset: int,
    packed_size: int,
    original_size: int,
    crc: int | None,
) -> Member:
    """Assemble one member, refusing bounds the archive cannot hold."""
    if offset + packed_size > len(data):
        raise NotAnLha(f"the member at offset {position} states more bytes than the archive holds")
    method = data[position + _I_METHOD : position + _I_METHOD + _METHOD_LENGTH]
    return Member(name, method, offset, packed_size, original_size, crc)


def _read_extended(data: bytes, start: int, first_size: int) -> tuple[bytes, bytes | None, int]:
    """Walk the extended-header chain: its directory, its name, and where it ends.

    A file-name header (``0x01``) overrides the base header's name where one
    is present, which is how a level-1 archive carries a name too long for
    the base header's byte and the only way a level-2 one carries a name at
    all.
    """
    position, size = start, first_size
    directory = b""
    name: bytes | None = None
    while size:
        if size < _EXT_MINIMUM or position + size > len(data):
            raise NotAnLha(f"an extended header at offset {position} states an impossible size")
        kind = data[position]
        payload = data[position + 1 : position + size - _EXT_SIZE_FIELD]
        if kind == _EXT_FILENAME:
            name = payload
        elif kind == _EXT_DIRNAME:
            directory = payload
        position += size
        size = _u16(data, position - _EXT_SIZE_FIELD)
    return directory, name, position


def _member_name(directory: bytes, name: bytes) -> str:
    """One member's path, both separators an LhA header can carry turned into ``/``."""
    text = (directory + name).decode("latin-1")
    joined = text.replace(_AMIGA_SEPARATOR, _SEPARATOR).replace(_DOS_SEPARATOR, _SEPARATOR)
    if not joined:
        # Every member has a name. Bytes that walk this far without one are
        # some other format read as a header — a zip under an .lha suffix
        # does exactly that — and a nameless member is a thing no listing can
        # state, so the walk refuses rather than answering one.
        raise NotAnLha("a member header states no name, which no LhA archive writes")
    return joined


def _optional_u16(data: bytes, offset: int, limit: int) -> int | None:
    """A 16-bit field that is there only where the header reaches it."""
    if offset + _EXT_SIZE_FIELD > limit or offset + _EXT_SIZE_FIELD > len(data):
        return None
    return _u16(data, offset)


def _u16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise NotAnLha(f"a header field at offset {offset} is past the end of the file")
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise NotAnLha(f"a header field at offset {offset} is past the end of the file")
    return struct.unpack_from("<I", data, offset)[0]


# ---------------------------------------------------------------------------
# The -lh5- stream.
# ---------------------------------------------------------------------------


class _Bits:
    """The compressed stream read most-significant bit first, zeros past its end.

    Reading past the end as zeros is what the format's own decoders do: a
    block's last symbol can need bits the encoder never wrote, because the
    symbol count, not the byte count, says when the stream is over.
    """

    __slots__ = ("_data", "_position")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._position = 0

    def peek(self, count: int) -> int:
        """The next *count* bits (at most 16) without consuming them."""
        start = self._position >> 3
        window = int.from_bytes(self._data[start : start + 4].ljust(4, b"\x00"), "big")
        return (window >> (32 - (self._position & 7) - count)) & ((1 << count) - 1)

    def skip(self, count: int) -> None:
        self._position += count

    @property
    def past_end(self) -> int:
        """How many bits the reader has run beyond the compressed bytes."""
        return self._position - len(self._data) * 8

    def take(self, count: int) -> int:
        value = self.peek(count)
        self._position += count
        return value


@dataclass(slots=True)
class _CodeTable:
    """One decoded Huffman table: a flat lookup, plus a tree for longer codes.

    ``table`` maps the next ``bits`` bits straight to a symbol wherever the
    code is that short; a value at or above ``count`` is a node in the
    ``left`` / ``right`` tree instead, walked one bit at a time. That split is
    the format's own — the tables are sized 12 and 8 bits — and it is why a
    symbol and a node index share one number space.
    """

    count: int
    bits: int
    lengths: list[int]
    table: list[int]
    left: list[int]
    right: list[int]


def _decode_symbol(bits: _Bits, codes: _CodeTable) -> int:
    """One symbol: the flat table, then the tree where the code outruns it."""
    symbol = codes.table[bits.peek(codes.bits)]
    if symbol >= codes.count:
        window = bits.peek(_MAX_CODE_LENGTH)
        mask = 1 << (_MAX_CODE_LENGTH - 1 - codes.bits)
        while symbol >= codes.count:
            symbol = codes.right[symbol] if window & mask else codes.left[symbol]
            mask >>= 1
    bits.skip(codes.lengths[symbol])
    return symbol


def _flat_codes(count: int, table_bits: int, symbol: int) -> _CodeTable:
    """The table a block states when one symbol carries the whole alphabet.

    It costs no bits at all: every lookup answers that symbol, and its length
    is zero, so the stream does not advance. The format spells this as a
    stated count of zero followed by the symbol, in a field wide enough to
    hold a number no alphabet here has — so the symbol is checked rather than
    used as an index into tables it would run past.
    """
    if symbol >= count:
        raise CorruptMember(f"a block states the single symbol {symbol} for an alphabet of {count}")
    return _CodeTable(
        count=count,
        bits=table_bits,
        lengths=[0] * count,
        table=[symbol] * (1 << table_bits),
        left=[0] * (2 * count),
        right=[0] * (2 * count),
    )


def _code_starts(lengths: list[int]) -> list[int]:
    """The first canonical code of each length, in the format's 16-bit code space.

    The lengths must use that space exactly: a code that leaves any of it
    unspent (or overspends it) decodes nothing reliably, so it is refused
    rather than half-built.
    """
    counts = [0] * (_MAX_CODE_LENGTH + 1)
    for length in lengths:
        counts[length] += 1
    starts = [0] * (_MAX_CODE_LENGTH + 2)
    for length in range(1, _MAX_CODE_LENGTH + 1):
        starts[length + 1] = starts[length] + (counts[length] << (_MAX_CODE_LENGTH - length))
    if starts[_MAX_CODE_LENGTH + 1] != _CODE_SPACE:
        raise CorruptMember("the Huffman code lengths in this block do not form a complete code")
    return starts


def _build_codes(count: int, table_bits: int, lengths: list[int]) -> _CodeTable:
    """Turn a table of code lengths into the lookup the decoder reads it through."""
    codes = _CodeTable(
        count=count,
        bits=table_bits,
        lengths=lengths,
        table=[0] * (1 << table_bits),
        left=[0] * (2 * count),
        right=[0] * (2 * count),
    )
    starts = _code_starts(lengths)
    spare = count
    shift = _MAX_CODE_LENGTH - table_bits
    for symbol in range(count):
        length = lengths[symbol]
        if length == 0:
            continue
        code = starts[length]
        starts[length] = code + (1 << (_MAX_CODE_LENGTH - length))
        if length <= table_bits:
            _fill_short(codes.table, code >> shift, starts[length] >> shift, symbol)
        else:
            spare = _add_long(codes, code, length, symbol, spare)
    return codes


def _fill_short(table: list[int], first: int, past: int, symbol: int) -> None:
    """Every lookup a code shorter than the table's width answers."""
    for index in range(first, past):
        table[index] = symbol


def _add_long(codes: _CodeTable, code: int, length: int, symbol: int, spare: int) -> int:
    """Hang one over-long code off the flat table, a bit per tree level.

    The table slot the code's first ``bits`` bits land in holds a node index
    instead of a symbol; each further bit picks that node's left or right
    child, and the last one holds the symbol. *spare* is the next unused node
    index — it starts past the alphabet so a node can never be read as a
    symbol — and the new one is returned.
    """
    index = code >> (_MAX_CODE_LENGTH - codes.bits)
    node = codes.table[index]
    if node == 0:
        node, spare = spare, spare + 1
        codes.table[index] = node
    bit = 1 << (_MAX_CODE_LENGTH - 1 - codes.bits)
    for _ in range(length - codes.bits - 1):
        branch = codes.right if code & bit else codes.left
        if branch[node] == 0:
            branch[node], spare = spare, spare + 1
        node = branch[node]
        bit >>= 1
    branch = codes.right if code & bit else codes.left
    branch[node] = symbol
    return spare


def _read_pre_length(bits: _Bits) -> int:
    """One length of the pre-code table: three bits, or the escape above six."""
    window = bits.peek(_MAX_CODE_LENGTH)
    length = window >> (_MAX_CODE_LENGTH - _PRE_SKIP_AFTER)
    if length == _PRE_ESCAPE:
        mask = 1 << (_MAX_CODE_LENGTH - _PRE_SKIP_AFTER - 1)
        while window & mask:
            mask >>= 1
            length += 1
    bits.skip(_PRE_SKIP_AFTER if length < _PRE_ESCAPE else length - _PRE_SKIP_AFTER)
    if length > _MAX_CODE_LENGTH:
        raise CorruptMember(f"a code length of {length} is longer than the format's code space")
    return length


def _read_pre_codes(bits: _Bits, count: int, count_bits: int, skip_after: int) -> _CodeTable:
    """A table whose lengths are sent literally — the pre-code and position tables.

    *skip_after* is the index at which a two-bit run says how many entries to
    pass over; the position table has no such irregularity and says -1.
    """
    stated = bits.take(count_bits)
    if stated == 0:
        return _flat_codes(count, _PRE_TABLE_BITS, bits.take(count_bits))
    if stated > count:
        raise CorruptMember(f"a block states {stated} code lengths for an alphabet of {count}")
    lengths = [0] * count
    index = 0
    while index < stated:
        lengths[index] = _read_pre_length(bits)
        index += 1
        if index == skip_after:
            index += min(bits.take(2), count - index)
    return _build_codes(count, _PRE_TABLE_BITS, lengths)


def _zero_run(bits: _Bits, code: int) -> int:
    """How many literal-table entries one of the three zero-run codes passes over."""
    if code == 0:
        return 1
    if code == 1:
        return bits.take(_RUN_SHORT_BITS) + _RUN_SHORT_BASE
    return bits.take(_CODE_COUNT_BITS) + _RUN_LONG_BASE


def _read_code_lengths(bits: _Bits, pre: _CodeTable) -> _CodeTable:
    """The literal and match-length table, its lengths sent through the pre-code table."""
    stated = bits.take(_CODE_COUNT_BITS)
    if stated == 0:
        return _flat_codes(_CODES, _CODE_TABLE_BITS, bits.take(_CODE_COUNT_BITS))
    if stated > _CODES:
        raise CorruptMember(f"a block states {stated} code lengths for an alphabet of {_CODES}")
    lengths = [0] * _CODES
    index = 0
    while index < stated:
        code = _decode_symbol(bits, pre)
        if code > 2:
            lengths[index] = code - 2
            index += 1
        else:
            index += min(_zero_run(bits, code), _CODES - index)
    return _build_codes(_CODES, _CODE_TABLE_BITS, lengths)


def _read_block_header(bits: _Bits) -> tuple[int, _CodeTable, _CodeTable]:
    """A block's symbol count and its two working tables, in the order sent."""
    remaining = bits.take(_BLOCK_COUNT_BITS)
    pre = _read_pre_codes(bits, _PRE_CODES, _PRE_COUNT_BITS, _PRE_SKIP_AFTER)
    codes = _read_code_lengths(bits, pre)
    positions = _read_pre_codes(bits, _POSITIONS, _POSITION_COUNT_BITS, -1)
    return remaining, codes, positions


def _copy_match(out: bytearray, bits: _Bits, positions: _CodeTable, symbol: int) -> None:
    """One back-reference: its length from the symbol, its distance from the position table."""
    length = symbol - _LITERALS + _THRESHOLD
    code = _decode_symbol(bits, positions)
    distance = ((1 << (code - 1)) + bits.take(code - 1) if code else 0) + 1
    if distance > len(out):
        raise CorruptMember("a match points before the start of the member")
    for _ in range(length):
        out.append(out[-distance])


def _refuse_impossible_size(original: int) -> None:
    """No member this package reads is anywhere near the ceiling, and past it nothing is decoded."""
    if original > _MAX_MEMBER_BYTES:
        raise CorruptMember(
            f"the header states {original} bytes, past the {_MAX_MEMBER_BYTES} this reader "
            "decodes — no member it is here to read is that size"
        )


def _inflate_lh5(packed: bytes, original: int) -> bytes:
    """Decode one ``-lh5-`` member: blocks of symbols over a sliding window."""
    _refuse_impossible_size(original)
    if original == 0:
        return b""
    bits = _Bits(packed)
    out = bytearray()
    remaining, codes, positions = _read_block_header(bits)
    while len(out) < original:
        if bits.past_end > _END_MARGIN_BITS:
            # The stream is spent and the member is not finished: the size in
            # the header is not the size these bytes decode to. Reading on
            # would be reading zeros, one invented literal per turn.
            raise CorruptMember(
                f"the compressed stream ends after {len(out)} of the {original} bytes the header "
                "states — the two do not describe the same member"
            )
        if remaining <= 0:
            remaining, codes, positions = _read_block_header(bits)
        remaining -= 1
        symbol = _decode_symbol(bits, codes)
        if symbol < _LITERALS:
            out.append(symbol)
        else:
            _copy_match(out, bits, positions, symbol)
    # A match can carry past the stated length; the count of bytes is what
    # ends the member, not the last symbol.
    return bytes(out[:original])
