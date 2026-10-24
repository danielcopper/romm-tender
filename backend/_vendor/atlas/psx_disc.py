"""Read a PlayStation disc's identity — the bytes its emulators decide the console region from — stdlib only.

Three emulators decide a disc's region, and all three read the same few things
off its first data track: the licence string the mastering wrote into sector 4,
and the ``BOOT`` line of ``SYSTEM.CNF`` in the ISO 9660 root (DuckStation
``src/core/system.cpp:1057-1121`` at RetroDECK/Duckstation@1f6de046b,
SwanStation ``src/core/system.cpp:418-473`` at libretro/swanstation@4d309c0,
Beetle PSX ``libretro.cpp:1410-1640`` at libretro/beetle-psx-libretro@d6383bf).
What each of them then *makes* of those bytes differs, and that is
:mod:`atlas.psx_region`'s business. This module reads the bytes once, and only
where all three would read the same bytes: a disc on which they would not is
refused rather than read the way one of them reads it, because a reading that
is true for one emulator and false for another is no identity of the disc.

What is read, and from where:

- a ``.cue`` sheet in DuckStation's grammar (``src/util/cue_parser.cpp:53-472``
  at 1f6de046b, which SwanStation shares), every file it names opened before
  anything is read, the way ``CDImageCueSheet::OpenAndParse`` opens them
  (``src/util/cd_image_cue.cpp:59-131``): one that will not open stops the open.
  DuckStation's one leniency there — a first file that will not open is looked
  for again as the cue's own name with ``.bin`` (:107-118) — is not followed,
  because neither other emulator follows it; such a disc answers as the missing
  file it names;
- a bare ``.bin``, ``.img`` or ``.iso`` as one raw track of 2352-byte sectors
  from offset 0 (``src/util/cd_image_bin.cpp:46-108``; SwanStation's
  ``src/common/cd_image.cpp:42-45`` takes the same three suffixes). A cooked
  2048-byte ``.iso`` read that way is the emulators' own misreading, and the
  sector-mode check below refuses it;
- track 1 only, and only as raw 2352-byte Mode 2 sectors (``MODE2/2352``) whose
  index 1 sits at the start of its file with no index 0 or ``PREGAP`` before
  it. Mode 2, because SwanStation copies the user data from byte 24 whatever the
  sector says (``src/common/cd_image.cpp:187-190`` at 4d309c0) while DuckStation
  and Beetle take the offset from the sector's own mode byte
  (``src/util/cd_image.cpp:269-282``; ``mednafen/cdrom/cdromif.cpp:573-580``).
  Index 1 at the head of the file, because DuckStation and SwanStation seek
  *within track 1* (``Seek(1, 4)``) and Beetle by absolute address
  (``ReadSector(buf, 4, 1)``), and the two meet only there. Every sector read
  must carry mode byte 2; one that does not is the misread ``.iso`` above, or a
  disc of another kind;
- the licence: the first :data:`LICENSE_LENGTH` user-data bytes of sector 4 —
  as many as Beetle searches (``libretro.cpp:1581``), which covers the strings
  the other two compare (``system.cpp:1078-1098``);
- the Primary Volume Descriptor at sector 16, where all three find it when it
  is there; a disc whose sector 16 is not one is refused, because the three
  then search on by three different rules (DuckStation
  ``src/util/iso_reader.cpp:55-83``; SwanStation ``src/common/iso_reader.cpp:33-57``;
  Beetle ``libretro.cpp:1421-1446``);
- ``SYSTEM.CNF`` in the root directory, looked up the three ways — DuckStation's
  (``iso_reader.cpp:85-207``: case-insensitive, version stripped, every sector
  of the directory), SwanStation's (``iso_reader.cpp:73-170``: a
  case-insensitive prefix with a ``;`` after it) and Beetle's
  (``libretro.cpp:1456-1478``: exactly ``SYSTEM.CNF;1``, records read until the
  first zero length byte). Where the three land on the same record its bytes are
  the identity's; where they do not, the disc is refused;
- whether the executable DuckStation's ``BOOT`` parse names exists on the disc
  as a file (``system.cpp:913-1001``, ``:1111-1113``) — DuckStation alone
  requires it before it trusts the serial;
- whether every sector Beetle's route may read passes its EDC check
  (``cdromif.cpp:538-584``, ``recover-raw.c:55-73`` and ``:183-205``). Beetle
  alone checks, and where a check fails it attempts a correction this module
  does not perform, so a failing sector is reported rather than judged.

An ``.m3u`` is not read here: each emulator resolves its own playlist, by its
own rules, before it opens the disc the playlist names — :mod:`atlas.psx_region`
reads the playlist as text and hands this module the entry. Nor is a CHD's
container: :mod:`atlas.psx_chd` reads its hunks and hands :func:`identity_of`
track 1's raw sectors, which are then read exactly as a bin file's are.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import BinaryIO, Callable, Iterator

RAW_SECTOR_SIZE = 2352
USER_DATA_SIZE = 2048
# A Mode 2 sector: 12 sync bytes, 4 header bytes, 8 subheader bytes, then data.
_MODE2_DATA_OFFSET = 24
_MODE_BYTE = 15
# Beetle checks a Mode 2 sector as Form 1: the subheader and the data, the
# checksum after them, little endian (recover-raw.c:58-66).
_EDC_SPAN = slice(16, 2072)
_EDC_FIELD = slice(2072, 2076)
LICENSE_SECTOR = 4
LICENSE_LENGTH = 0x48
PVD_SECTOR = 16
# Where a Primary Volume Descriptor keeps its root record, and how long a
# directory record's fixed part is (ECMA-119 9.1; iso_reader.h:79, :119-137).
_PVD_ROOT_RECORD = 156
_RECORD_HEADER_SIZE = 33
_FLAG_DIRECTORY = 0x02
# What a directory sector is called where one lies past the end of track 1.
_DIRECTORY_SECTOR = "a directory sector"
# Beetle's "arbitrary sanity check" on the root directory's length
# (libretro.cpp:1450-1455): at this size or more it reads no SYSTEM.CNF.
BEETLE_ROOT_LIMIT = 1024 * 1024 * 10
# atlas's own bounds on what the disc's own records make it read, each far past
# a real disc, a read past one answering Unreadable: a file of a megabyte (a
# SYSTEM.CNF fits one sector; it is read whole, so its stated length is checked
# before the read), the first 16 MiB of a directory (past Beetle's own 10 MiB
# for the root; a walk stops at its match first, so only one that reads on past
# the bound is refused), and 2^17 sector reads in one identity — a path of a
# directory that names itself, walked once per component, ends there.
MAX_FILE_BYTES = 1 << 20
MAX_DIRECTORY_BYTES = 16 << 20
MAX_SECTOR_READS = 1 << 17
# The most of a cue sheet read, as much as a playlist may hold
# (atlas.psx_region): a sheet of 99 tracks fits a few KiB.
MAX_CUE_BYTES = 4 << 20
SYSTEM_CNF = b"SYSTEM.CNF"
SYSTEM_CNF_RECORD = b"SYSTEM.CNF;1"
FALLBACK_EXECUTABLE = b"PSX.EXE"
CUE_SUFFIX = ".cue"
BARE_IMAGE_SUFFIXES = (".bin", ".img", ".iso")
_RAW_MODE2 = "MODE2/2352"
_AUDIO = "AUDIO"
# Every mode the parser accepts (cue_parser.cpp:265-285).
_CUE_MODES = frozenset(
    {_AUDIO, "MODE1/2048", "MODE1/2352", "MODE2/2336", "MODE2/2048", "MODE2/2342", "MODE2/2332", _RAW_MODE2}
)
# The commands the parser reads past (cue_parser.cpp:178-210).
_CUE_IGNORED = frozenset(
    {
        "REM",
        "POSTGAP",
        "CATALOG",
        "CDTEXTFILE",
        "ISRC",
        "TRACK_ISRC",
        "TITLE",
        "PERFORMER",
        "SONGWRITER",
        "COMPOSER",
        "ARRANGER",
        "MESSAGE",
        "DISC_ID",
        "GENRE",
        "TOC_INFO1",
        "TOC_INFO2",
        "UPC_EAN",
        "SIZE_INFO",
    }
)
# What C's ``isspace`` holds in the "C" locale.
C_SPACE = b" \t\n\v\f\r"
_CUE_TOKEN = re.compile(rb"[^ \t\n\v\f\r]+")
_MSF = re.compile(rb"(\d+)[ \t\n\v\f\r]*:(\d+)[ \t\n\v\f\r]*:(\d+)")
_DECIMAL = re.compile(rb"[+-]?\d+")
# The EDC polynomial 0x8001801B, reflected (edc_crc32.c:31-38).
_EDC_POLY = 0xD8018001


def _edc_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = (crc >> 1) ^ (_EDC_POLY if crc & 1 else 0)
        table.append(crc)
    return tuple(table)


_EDC_TABLE = _edc_table()


def edc(data: bytes) -> int:
    """``EDCCrc32`` (edc_crc32.c:122-130): the sector checksum, zero-initialised, no final inversion."""
    crc = 0
    for byte in data:
        crc = _EDC_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc


class NotADisc(Exception):
    """The content opens as no disc: a cue sheet the parser refuses, or one whose track 1 is audio."""


class FormatNotRead(Exception):
    """A disc this module does not read: a suffix it has no reader for, or a layout the three emulators read apart."""


class CodecUnavailable(Exception):
    """The image is stored with a codec atlas reads, and this runtime lacks the module that decodes it."""


class Unreadable(Exception):
    """The image's bytes did not come back as a whole: a block that does not decode, or a bound atlas reads within."""


@dataclass(frozen=True, slots=True)
class DiscIdentity:
    """What the three emulators read off one disc before they decide its region.

    ``license`` is the first :data:`LICENSE_LENGTH` user-data bytes of sector
    4. ``system_cnf`` is the file all three lookups land on, its whole length as
    DuckStation and SwanStation read it (``iso_reader.cpp:353-376`` at 1f6de046b,
    ``src/common/iso_reader.cpp:261-291`` at 4d309c0), and
    ``None`` where none of them finds one; ``system_cnf_sector`` is the first
    sector of that same file, which is what Beetle reads — 2048 bytes from the
    file's first block whatever the file's length (``libretro.cpp:1466-1474``),
    so the file's bytes and whatever pads that sector after them.
    ``boot_found`` says whether the executable DuckStation's ``BOOT`` parse
    names — ``PSX.EXE`` where there is no such line — exists on the disc as a
    file. ``edc_valid`` says whether every sector Beetle's route may read passes
    Beetle's checksum: sector 4, sector 16, the root directory as far as Beetle
    reads it, and the first sector of ``SYSTEM.CNF``.
    """

    license: bytes
    system_cnf: bytes | None
    system_cnf_sector: bytes | None
    boot_found: bool
    edc_valid: bool

    def __post_init__(self) -> None:
        if len(self.license) != LICENSE_LENGTH:
            raise ValueError(f"DiscIdentity: license is the {LICENSE_LENGTH} bytes read, got {len(self.license)}")
        if (self.system_cnf is None) != (self.system_cnf_sector is None):
            raise ValueError("DiscIdentity: SYSTEM.CNF and its first sector are found together or not at all")
        if self.system_cnf_sector is not None and len(self.system_cnf_sector) != USER_DATA_SIZE:
            raise ValueError(f"DiscIdentity: a sector is {USER_DATA_SIZE} bytes, got {len(self.system_cnf_sector)}")


Opener = Callable[[str], BinaryIO]
# Track 1's raw 2352-byte sector at an index from its start, or ``None`` past
# its end — what a bin file holds at ``index * 2352``, and what a CHD's frame
# holds before its subcode.
RawSectors = Callable[[int], bytes | None]


# ── The cue sheet ──────────────────────────────────────────────────────────


def _cue_tokens(line: bytes) -> list[bytes]:
    """``GetToken`` over one line (cue_parser.cpp:86-125): space-separated, a quoted token up to its closing quote.

    An unterminated quote ends the line's tokens, as the C returns an empty
    view there.
    """
    tokens: list[bytes] = []
    rest = line.lstrip(C_SPACE)
    while rest:
        if rest[:1] == b'"':
            end = rest.find(b'"', 1)
            if end < 0:
                break
            tokens.append(rest[1:end])
            rest = rest[end + 1 :]
        else:
            match = _CUE_TOKEN.match(rest)
            assert match is not None  # rest is non-empty and starts with no space
            tokens.append(match.group())
            rest = rest[match.end() :]
        rest = rest.lstrip(C_SPACE)
    return tokens


def _s32(text: bytes) -> int | None:
    """``StringUtil::FromChars<s32>``: a whole decimal that fits a signed 32-bit integer, ``None`` otherwise.

    The digits are counted before any conversion, so a number of any length
    is refused the way ``std::from_chars`` refuses an overflow, rather than
    handed to an unbounded conversion.
    """
    if _DECIMAL.fullmatch(text) is None or len(text.lstrip(b"+-").lstrip(b"0")) > 10:
        return None
    value = int(text)
    return value if -(2**31) <= value < 2**31 else None


def _msf(token: bytes) -> int | None:
    """``GetMSF`` (cue_parser.cpp:127-170): ``mm:ss:ff`` as a frame count, ``None`` where the parser refuses it."""
    match = _MSF.fullmatch(token)
    if match is None:
        return None
    parts = [_s32(part) for part in match.groups()]
    if any(part is None for part in parts):
        return None
    minute, second, frame = (part or 0 for part in parts)
    if second > 60 or frame > 75:
        return None
    return (minute * 60 + second) * 75 + frame


def _number(tokens: list[bytes], index: int) -> int | None:
    """``StringUtil::FromChars<s32>`` over one token: a whole decimal, nothing after it."""
    return None if index >= len(tokens) else _s32(tokens[index])


@dataclass
class _CueTrack:
    number: int
    file: bytes
    mode: str
    indices: dict[int, int]
    pregap: bool = False


class _CueSheet:
    """DuckStation's cue grammar: the commands of ``CueParser::File`` and the lines it refuses (cue_parser.cpp:53-472)."""

    def __init__(self) -> None:
        self.tracks: list[_CueTrack] = []
        self._file: bytes | None = None
        self._track: _CueTrack | None = None

    def line(self, raw: bytes) -> None:
        tokens = _cue_tokens(raw)
        if not tokens:
            return
        command = tokens[0].decode("latin-1").upper()
        if command in _CUE_IGNORED:
            return
        handlers = {
            "FILE": self._file_command,
            "TRACK": self._track_command,
            "INDEX": self._index_command,
            "PREGAP": self._pregap_command,
            "FLAGS": self._flags_command,
        }
        handler = handlers.get(command)
        if handler is None:
            raise NotADisc(f"the cue sheet holds a command the parser refuses ({command!r}, cue_parser.cpp:212)")
        handler(tokens[1:])

    def _file_command(self, args: list[bytes]) -> None:
        if not args or not args[0]:
            raise NotADisc("a FILE command names no file (cue_parser.cpp:221-225)")
        if len(args) < 2 or args[1].upper() != b"BINARY":
            raise NotADisc("a FILE command is not BINARY (cue_parser.cpp:227-231)")
        self._file = args[0]

    def _track_command(self, args: list[bytes]) -> None:
        self.complete()
        if self._file is None:
            raise NotADisc("a TRACK comes before any FILE (cue_parser.cpp:243-247)")
        number = _number(args, 0)
        if number is None or not 1 <= number <= 99:
            raise NotADisc("a TRACK number is missing or outside 1-99 (cue_parser.cpp:249-261)")
        mode = args[1].decode("latin-1").upper() if len(args) > 1 else ""
        if mode not in _CUE_MODES:
            raise NotADisc(f"a TRACK mode the parser refuses ({mode!r}, cue_parser.cpp:263-285)")
        self._track = _CueTrack(number, self._file, mode, {})

    def _current(self, what: str) -> _CueTrack:
        if self._track is None:
            raise NotADisc(f"{what} stands outside a track (cue_parser.cpp:296-300, :342-346, :374-378)")
        return self._track

    def _index_command(self, args: list[bytes]) -> None:
        track = self._current("an INDEX")
        number = _number(args, 0)
        if number is None or not 0 <= number <= 99 or number in track.indices:
            raise NotADisc("an INDEX number is missing, outside 0-99 or repeated (cue_parser.cpp:302-320)")
        frames = _msf(args[1]) if len(args) > 1 else None
        if frames is None:
            raise NotADisc("an INDEX location the parser refuses (cue_parser.cpp:322-334)")
        track.indices[number] = frames

    def _pregap_command(self, args: list[bytes]) -> None:
        track = self._current("a PREGAP")
        if track.pregap or not args or _msf(args[0]) is None:
            raise NotADisc("a PREGAP that is repeated or has no valid location (cue_parser.cpp:348-366)")
        track.pregap = True

    def _flags_command(self, _args: list[bytes]) -> None:
        # Every flag word is accepted or warned about; only the track matters.
        self._current("FLAGS")

    def complete(self) -> None:
        """``CompleteLastTrack`` (cue_parser.cpp:401-436): index 1 present, the indices in order."""
        track = self._track
        if track is None:
            return
        if 1 not in track.indices:
            raise NotADisc(f"track {track.number} has no index 1 (cue_parser.cpp:407-412)")
        for number, frames in track.indices.items():
            previous = track.indices.get(number - 1)
            if number and previous is not None and previous > frames:
                raise NotADisc(f"track {track.number}'s indices run backwards (cue_parser.cpp:415-426)")
        if 0 in track.indices:
            # An index 0 beside a PREGAP drops the PREGAP (:428-433).
            track.pregap = False
        self.tracks.append(track)
        self._track = None


# What ``std::fgets`` reads into the parser's 1024-byte buffer at most
# (cue_parser.cpp:55-57): a longer line arrives as several.
_CUE_LINE = 1023


def _fgets_lines(data: bytes) -> list[bytes]:
    """The lines ``fgets`` hands the parser: ended by ``\\n`` alone, at most 1023 bytes each, cut at a NUL.

    ``GetToken`` walks a C string, so whatever follows a NUL in a line is
    never seen.
    """
    pieces = data.split(b"\n")
    whole = [piece + b"\n" for piece in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else [])
    lines: list[bytes] = []
    for raw in whole:
        lines.extend(raw[i : i + _CUE_LINE].split(b"\x00", 1)[0] for i in range(0, len(raw), _CUE_LINE))
    return lines


def parse_cue(data: bytes) -> list[_CueTrack]:
    """The tracks a cue sheet declares, or :class:`NotADisc` where DuckStation's parser stops."""
    sheet = _CueSheet()
    for raw in _fgets_lines(data):
        sheet.line(raw)
    sheet.complete()
    if not any(track.number == 1 for track in sheet.tracks):
        raise NotADisc("the cue sheet declares no track 1, so the image has no first track (cd_image_cue.cpp:84-90)")
    return sheet.tracks


def _beside(path: str, name: bytes) -> str:
    """A file name a cue sheet spells, joined onto the sheet's own directory unless it is absolute (:96-98)."""
    spelled = os.fsdecode(name)
    return spelled if os.path.isabs(spelled) else os.path.join(os.path.dirname(path), spelled)


def _track1_of_cue(path: str, opener: Opener) -> str:
    """The file holding track 1 of the cue sheet at *path*, once every file the sheet names has opened."""
    with opener(path) as handle:
        data = handle.read(MAX_CUE_BYTES + 1)
    if len(data) > MAX_CUE_BYTES:
        raise Unreadable(f"a cue sheet of more than atlas's bound of {MAX_CUE_BYTES} bytes")
    tracks = parse_cue(data)
    for full in dict.fromkeys(_beside(path, track.file) for track in tracks):
        opener(full).close()
    track = next(t for t in tracks if t.number == 1)
    if track.mode == _AUDIO:
        raise NotADisc("track 1 is an audio track, so the disc has no data track to read a region from")
    if track.mode != _RAW_MODE2 or 0 in track.indices or track.pregap or track.indices[1] != 0:
        raise FormatNotRead(
            "track 1 is not raw Mode 2 starting at the head of its file, the one layout the three emulators read alike"
        )
    return _beside(path, track.file)


# ── The sectors and the filesystem ────────────────────────────────────────


class _Sectors:
    """Track 1's sectors, each read once, the mode checked and Beetle's checksum noted."""

    def __init__(self, raw: RawSectors) -> None:
        self._raw = raw
        self._read: dict[int, bytes | None] = {}
        self._reads = 0
        self.failing: set[int] = set()

    def user(self, lba: int, *, keep: bool = True) -> bytes | None:
        """The 2048 user-data bytes of the Mode 2 sector at *lba*, or ``None`` past the end of the track.

        A directory is walked by three lookups and may be long, so its sectors
        are read without being kept (*keep*): what a walk costs in memory stays
        one sector, while its checksum is still noted.
        """
        if lba in self._read:
            return self._read[lba]
        self._reads += 1
        if self._reads > MAX_SECTOR_READS:
            raise Unreadable(f"an identity read past atlas's bound of {MAX_SECTOR_READS} sector reads")
        raw = self._raw(lba)
        data = None if raw is None or len(raw) != RAW_SECTOR_SIZE else self._data(lba, raw)
        if keep:
            self._read[lba] = data
        return data

    def _data(self, lba: int, raw: bytes) -> bytes:
        if raw[_MODE_BYTE] != 2:
            raise FormatNotRead(
                f"sector {lba} of track 1 is not a Mode 2 sector, and the emulators take its data from different "
                "offsets then — the shape of a cooked 2048-byte image read as raw, or of a disc of another kind"
            )
        if edc(raw[_EDC_SPAN]) != int.from_bytes(raw[_EDC_FIELD], "little"):
            self.failing.add(lba)
        return raw[_MODE2_DATA_OFFSET : _MODE2_DATA_OFFSET + USER_DATA_SIZE]

    def required(self, lba: int, what: str, *, keep: bool = True) -> bytes:
        data = self.user(lba, keep=keep)
        if data is None:
            raise FormatNotRead(f"{what} (sector {lba}) lies past the end of track 1")
        return data


@dataclass(frozen=True, slots=True)
class _Record:
    """One ISO 9660 directory record — the four fields a lookup reads (iso_reader.h:119-137)."""

    location: int
    length: int
    directory: bool
    name: bytes


def _record(sector: bytes, offset: int) -> tuple[int, _Record]:
    """The record at *offset*, and the length byte that says how far the next one is."""
    length = sector[offset]
    name_length = sector[offset + 32]
    start = offset + _RECORD_HEADER_SIZE
    return length, _Record(
        location=int.from_bytes(sector[offset + 2 : offset + 6], "little"),
        length=int.from_bytes(sector[offset + 10 : offset + 14], "little"),
        directory=bool(sector[offset + 25] & _FLAG_DIRECTORY),
        name=sector[start : start + name_length],
    )


def _sector_count(length: int) -> int:
    return (length + USER_DATA_SIZE - 1) // USER_DATA_SIZE


def _bounded_sectors(length: int, limit: int, what: str) -> int:
    """The sectors a record's *length* spans, or :class:`Unreadable` where the length is past atlas's *limit*."""
    if length > limit:
        raise Unreadable(f"{what} of {length} bytes, past atlas's bound of {limit}")
    return _sector_count(length)


def _duckstation_name(name: bytes) -> bytes:
    """``GetDirectoryEntryFileName`` (iso_reader.cpp:99-126): up to ``;`` or NUL, the two dot names spelled out."""
    if name == b"\x00":
        return b"."
    if name == b"\x01":
        return b".."
    return re.split(rb"[;\x00]", name, maxsplit=1)[0]


def _swanstation_matches(record: _Record, component: bytes) -> bool:
    """SwanStation's name test (iso_reader.cpp:127-152 at 4d309c0): a directory by its whole name, a file by a
    case-insensitive prefix with ``;`` after it."""
    name = record.name
    if name in (b"\x00", b"\x01") or len(name) < len(component):
        return False
    prefix_equal = name[: len(component)].lower() == component.lower()
    if record.directory:
        return len(name) == len(component) and prefix_equal
    return prefix_equal and name[len(component) : len(component) + 1] == b";"


class _Filesystem:
    """The ISO 9660 reads the three emulators make over one track 1."""

    def __init__(self, sectors: _Sectors, root: _Record) -> None:
        self.sectors = sectors
        self.root = root

    def _directory_sectors(self, directory: _Record) -> Iterator[bytes]:
        """The directory's sectors one at a time, as a walk reaches them.

        A walk that reads on past :data:`MAX_DIRECTORY_BYTES` is
        :class:`Unreadable` only where it gets there: one that stops at its
        match before then answers as the emulator does.
        """
        for index in range(_sector_count(directory.length)):
            if index * USER_DATA_SIZE >= MAX_DIRECTORY_BYTES:
                raise Unreadable(
                    f"a directory of {directory.length} bytes, walked past atlas's bound of {MAX_DIRECTORY_BYTES}"
                )
            yield self.sectors.required(directory.location + index, _DIRECTORY_SECTOR, keep=False)

    def _duckstation_records(self, directory: _Record) -> Iterator[_Record]:
        """``LocateFile``'s walk (iso_reader.cpp:161-203): sector by sector, records until a short one, read only as
        far as the caller asks — DuckStation stops at the first match."""
        for sector in self._directory_sectors(directory):
            offset = 0
            while offset + _RECORD_HEADER_SIZE < USER_DATA_SIZE:
                length, record = _record(sector, offset)
                if length < _RECORD_HEADER_SIZE:
                    break
                offset += length
                yield record

    def duckstation_locate(self, path: bytes) -> _Record | None:
        """``IsoReader::LocateFile`` (iso_reader.cpp:85-207): the path split on ``/`` and ``\\``, each component
        compared case-insensitively with the version stripped, directories recursed into."""
        components = [part for part in re.split(rb"[/\\]", path) if part]
        if not components:
            return self.root
        found: _Record | None = self.root
        for component in components:
            if found is None or not found.directory or found.length == 0:
                return None
            found = next(
                (
                    record
                    for record in self._duckstation_records(found)
                    if _duckstation_name(record.name) not in (b"", b".", b"..")
                    and _duckstation_name(record.name).lower() == component.lower()
                ),
                None,
            )
        return found

    def swanstation_root_file(self, component: bytes) -> _Record | None:
        """SwanStation's root lookup of a file (iso_reader.cpp:73-170 at 4d309c0), the record bounds checked as read."""
        for sector in self._directory_sectors(self.root):
            offset = 0
            while offset + _RECORD_HEADER_SIZE < USER_DATA_SIZE:
                length, record = _record(sector, offset)
                if offset + length > USER_DATA_SIZE or len(record.name) > length or length < _RECORD_HEADER_SIZE:
                    break
                offset += length
                if _swanstation_matches(record, component):
                    return record
        return None

    def beetle_root_file(self) -> tuple[_Record | None, set[int]]:
        """Beetle's root scan (libretro.cpp:1456-1478) and the sectors it read.

        The directory is read as one stream until a zero length byte or the
        directory's end, and the first record named exactly ``SYSTEM.CNF;1`` is
        taken. The stream reads a sector only once a record reaches into it, so
        the sectors returned are those the scan actually touched. A root
        directory of :data:`BEETLE_ROOT_LIMIT` bytes or more is not read at all
        (libretro.cpp:1450-1455): Beetle's SYSTEM.CNF route ends there, and the
        licence decides.
        """
        if self.root.length >= BEETLE_ROOT_LIMIT:
            return None, set()
        sectors = self._directory_sectors(self.root)
        stream = bytearray()

        def reach(end: int) -> None:
            # Beetle's stream reads only as far as a record reaches (:1462-1471).
            while len(stream) < end:
                stream.extend(next(sectors, b"\x00" * USER_DATA_SIZE))

        position = 0
        while position < self.root.length:
            reach(position + 1)
            length = stream[position]
            if length == 0:
                # The zero byte that stopped the scan was read too.
                return None, _span(self.root.location, position + 1)
            reach(position + length)
            _, record = _record((bytes(stream[position : position + length]) + bytes(256))[:256], 0)
            position += length
            if record.name == SYSTEM_CNF_RECORD:
                return record, _span(self.root.location, position)
        return None, _span(self.root.location, position)

    def file_bytes(self, record: _Record) -> bytes:
        """``IsoReader::ReadFile`` (iso_reader.cpp:353-376): every sector of the file, cut to its length."""
        count = _bounded_sectors(record.length, MAX_FILE_BYTES, "a file")
        data = b"".join(self.sectors.required(record.location + index, "a file sector") for index in range(count))
        return data[: record.length]


def _span(start: int, end: int) -> set[int]:
    """The root sectors a stream read from the directory's start up to byte *end* (exclusive) touched."""
    return set(range(start, start + max(end - 1, 0) // USER_DATA_SIZE + 1))


# ── SYSTEM.CNF as DuckStation and SwanStation tokenise it ─────────────────


def boot_value(system_cnf: bytes) -> bytes | None:
    """The ``BOOT`` value as DuckStation and SwanStation tokenise ``SYSTEM.CNF`` (system.cpp:913-963 at 1f6de046b;
    system.cpp:283-330 at 4d309c0).

    Whitespace is dropped wherever it stands, a line is a key up to the first
    ``=`` and a value after it, and the first key spelled ``boot`` in any case
    wins. ``None`` where no line has that key.
    """
    lines: list[tuple[bytes, bytes]] = []
    key, value, in_value = bytearray(), bytearray(), False
    for byte in system_cnf:
        if byte in (0x0A, 0x0D):
            if key:
                lines.append((bytes(key), bytes(value)))
                key, value, in_value = bytearray(), bytearray(), False
        elif byte == 0x20 or 0x09 <= byte <= 0x0D:
            continue
        elif byte == 0x3D and not in_value:
            in_value = True
        else:
            (value if in_value else key).append(byte)
    if key:
        lines.append((bytes(key), bytes(value)))
    return next((v for k, v in lines if k.lower() == b"boot"), None)


def duckstation_executable(system_cnf: bytes | None) -> bytes:
    """``GetExecutableNameForImage(iso, false)`` (system.cpp:913-1001): the path DuckStation looks the executable up by.

    ``PSX.EXE`` where there is no ``SYSTEM.CNF`` or no ``BOOT`` line in it
    (:917-919, :961-963); otherwise the value with a leading ``cdrom:`` and
    every slash after it removed, and cut at the last ``;`` (:984-1001).
    """
    value = None if system_cnf is None else boot_value(system_cnf)
    if value is None:
        return FALLBACK_EXECUTABLE
    if value[:6] == b"cdrom:":
        value = value[6:]
    value = value.lstrip(b"/\\")
    cut = value.rfind(b";")
    return value if cut < 0 else value[:cut]


# ── The identity ──────────────────────────────────────────────────────────


def _system_cnf(filesystem: _Filesystem) -> tuple[_Record | None, set[int]]:
    """The one ``SYSTEM.CNF`` record all three lookups land on, and the root sectors Beetle read finding it."""
    duckstation = filesystem.duckstation_locate(SYSTEM_CNF)
    if duckstation is not None and duckstation.directory:
        duckstation = None
    beetle, touched = filesystem.beetle_root_file()
    if len({duckstation, filesystem.swanstation_root_file(SYSTEM_CNF), beetle}) != 1:
        raise FormatNotRead(
            "the three emulators' lookups of SYSTEM.CNF land on different records of the root directory — a name "
            "in another case or without its version, or a record past where Beetle stops reading"
        )
    return beetle, touched


def _file_sectors(handle: BinaryIO) -> RawSectors:
    """A bin file as raw sectors: one 2352-byte sector after another from its first byte."""

    def raw(lba: int) -> bytes:
        handle.seek(lba * RAW_SECTOR_SIZE)
        return handle.read(RAW_SECTOR_SIZE)

    return raw


def identity_of(raw: RawSectors) -> DiscIdentity:
    """The identity read off track 1's raw sectors, whatever image holds them.

    Raises :class:`FormatNotRead` where the three emulators would read the
    track apart, and :class:`Unreadable` where the disc's own records would
    make the read pass one of atlas's bounds (:data:`MAX_FILE_BYTES`,
    :data:`MAX_DIRECTORY_BYTES`, :data:`MAX_SECTOR_READS`); whatever *raw*
    raises passes through.
    """
    sectors = _Sectors(raw)
    license_data = sectors.required(LICENSE_SECTOR, "the licence")
    pvd = sectors.required(PVD_SECTOR, "the Primary Volume Descriptor")
    if pvd[1:6] != b"CD001" or pvd[0] != 1:
        raise FormatNotRead(
            "sector 16 holds no Primary Volume Descriptor, and the three emulators search on for one by three "
            "different rules"
        )
    _, root = _record(pvd, _PVD_ROOT_RECORD)
    filesystem = _Filesystem(sectors, root)
    record, beetle_read = _system_cnf(filesystem)
    system_cnf = None if record is None else filesystem.file_bytes(record)
    first = None if record is None else sectors.required(record.location, "SYSTEM.CNF's first block")
    if record is not None:
        beetle_read.add(record.location)
    executable = filesystem.duckstation_locate(duckstation_executable(system_cnf))
    beetle_read |= {LICENSE_SECTOR, PVD_SECTOR}
    return DiscIdentity(
        license=license_data[:LICENSE_LENGTH],
        system_cnf=system_cnf,
        system_cnf_sector=first,
        boot_found=executable is not None and not executable.directory,
        edc_valid=not (sectors.failing & beetle_read),
    )


def _identity_of(track1: str, opener: Opener) -> DiscIdentity:
    with opener(track1) as handle:
        return identity_of(_file_sectors(handle))


def read_identity(path: str, opener: Opener) -> DiscIdentity:
    """The identity of the disc at *path*, a ``.cue`` or a bare ``.bin`` / ``.img`` / ``.iso``.

    *opener* opens a path for binary reading, and its ``OSError`` propagates
    untouched, so the caller keeps the plain read's difference between a file
    that is not there and one that will not open. Raises :class:`NotADisc`,
    :class:`FormatNotRead` and :class:`Unreadable` as documented on them and on
    :func:`identity_of` — :class:`Unreadable` too for a cue sheet longer than
    :data:`MAX_CUE_BYTES`. The suffix decides the reader
    the way both emulators that open these formats decide it, without regard to
    case (cd_image.cpp:81-89).
    """
    suffix = os.path.splitext(path)[1].lower()
    if suffix == CUE_SUFFIX:
        track1 = _track1_of_cue(path, opener)
    elif suffix in BARE_IMAGE_SUFFIXES:
        track1 = path
    else:
        raise FormatNotRead(f"no reader here opens a {suffix or 'suffix-less'} image")
    return _identity_of(track1, opener)
