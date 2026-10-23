"""Which console region a PlayStation emulator decides for one game's content — each emulator's own rule, ported.

Three emulators pick the BIOS a launch boots by the console region, and under
their shipped setting that region is the running disc's own: DuckStation
(``src/core/system.cpp:1595-1648`` at RetroDECK/Duckstation@1f6de046b),
SwanStation (``src/core/system.cpp:638-655`` at libretro/swanstation@4d309c0)
and Beetle PSX, both builds (``libretro.cpp:1560-1640`` and ``:1980-1983`` at
libretro/beetle-psx-libretro@d6383bf). Each reads the same two things off the
disc — the licence string and the ``BOOT`` line of ``SYSTEM.CNF``, which
:mod:`atlas.psx_disc` reads once — and each makes its own decision out of them,
so each is ported here exactly rather than shared:

- **DuckStation** compares the licence string first (``system.cpp:1078-1103``),
  and only where it matches none of the three reads the ``BOOT`` line, requires
  the executable it names to exist on the disc (:1110-1113), and matches the
  serial's prefix by "starts with", ignoring case, over its eleven prefixes in
  order (:1057-1076, :1115-1120). A disc it cannot place — ``Other`` or
  ``NonPS1`` — boots as NTSC-U (:812-823, :1644-1649).
- **SwanStation** compares the same three licence strings first
  (``system.cpp:440-466``), then cuts the ``BOOT`` value at its last ``\\`` or
  ``:`` (:334-348) and at its last ``;`` (:362-366), turns ``SCES_123.45`` into
  ``SCES-12345`` (:228-256) and compares the leading letters, lowered,
  *exactly* against its sets (:418-438, :468-472) — no executable check.
  ``Other`` boots as NTSC-U (:649-654).
- **Beetle PSX** reads ``SYSTEM.CNF`` *first* (``libretro.cpp:1572``) with its own
  four-character grammar over ``cdrom:\\`` (:1480-1531), and only where that
  names no region searches the licence sector's printable bytes, lowered, for
  words (:1574-1620). The default is NTSC-U (``mednafen/settings.c:55-56``, with
  autodetection fixed on at :98-99), and only the first disc of a playlist moves
  it (:1572, :1597-1615).

An ``.m3u`` is each emulator's own to resolve, and all three decide the region
from its first entry (D1 of #661): DuckStation and SwanStation open the
playlist's first entry before anything else (``src/util/cd_image_m3u.cpp:76-115``
at 1f6de046b; ``src/common/cd_image_m3u.cpp:64-100`` at 4d309c0), and Beetle
decides from disc 0 (``libretro.cpp:4048-4090`` reads the list). The two
readers part on details this module follows: DuckStation and SwanStation trim
both ends of a line, skip a ``#`` after leading space and skip a line of one
character; Beetle skips ``#`` only in the first column, trims only the right,
reads at most 2047 bytes as one line, and follows a nested ``.m3u`` in place.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from . import psx_disc, qt_ini
from .machine import (
    DISC_FORMAT_NOT_READ,
    DISC_MISSING,
    DISC_NOT_A_DISC,
    DISC_OK,
    DISC_UNREADABLE,
    READ_MISSING,
    READ_OK,
    READ_UNREADABLE,
    BoundedReadingMachine,
    ReadResult,
)
from .placement import (
    DISC_REGIONS,
    REASON_CONTENT_UNOPENABLE,
    REASON_DISC_FORMAT_UNREAD,
    REASON_DISC_UNREAD,
    REGION_NTSC_J,
    REGION_NTSC_U,
    REGION_PAL,
    ConsoleRegion,
)

if TYPE_CHECKING:
    from .machine import Machine

RegionSource = Literal["setting", "custom-property", "license", "serial", "emulator-default"]
REGION_SOURCE_SETTING: RegionSource = "setting"
"""The emulator's console-region setting names one region, so no disc is consulted."""
REGION_SOURCE_CUSTOM_PROPERTY: RegionSource = "custom-property"
"""DuckStation's per-path ``Region`` property names the region, which the emulator takes over the disc's own."""
REGION_SOURCE_LICENSE: RegionSource = "license"
"""The licence string the disc carries in sector 4 placed it."""
REGION_SOURCE_SERIAL: RegionSource = "serial"
"""The serial the disc's ``BOOT`` line names placed it, by the emulator's own prefix rule."""
REGION_SOURCE_EMULATOR_DEFAULT: RegionSource = "emulator-default"
"""The disc was read and neither its licence string nor its serial placed it, so the emulator boots its default."""
# Where a decided region came from, in the order the emulators consult them.
REGION_SOURCES = (
    REGION_SOURCE_SETTING,
    REGION_SOURCE_CUSTOM_PROPERTY,
    REGION_SOURCE_LICENSE,
    REGION_SOURCE_SERIAL,
    REGION_SOURCE_EMULATOR_DEFAULT,
)

DiscUnreadStatus = Literal["missing", "unreadable", "not-a-disc", "sector-invalid", "boot-line-absent"]
DISC_UNREAD_MISSING: DiscUnreadStatus = "missing"
"""The image, or a file it names, is not there."""
DISC_UNREAD_UNREADABLE: DiscUnreadStatus = "unreadable"
"""The image, a file it names, or the playlist naming it is there and its bytes did not come back."""
DISC_UNREAD_NOT_A_DISC: DiscUnreadStatus = "not-a-disc"
"""The image opens as no disc: a cue sheet the parser refuses, or one whose first track is audio."""
DISC_UNREAD_SECTOR_INVALID: DiscUnreadStatus = "sector-invalid"
"""A sector Beetle reads fails its checksum, and whether its correction recovers the sector is not established."""
DISC_UNREAD_BOOT_LINE_ABSENT: DiscUnreadStatus = "boot-line-absent"
"""Beetle's ``SYSTEM.CNF`` read finds no ``BOOT``, which the core dereferences anyway (``libretro.cpp:1477``)."""
# Why a disc that was named decided no region — the ``status`` a
# ``disc-unread`` statement carries. The first three are the seam's words for
# the image; the last two are where one emulator's own rule cannot be followed
# over a disc that was read.
DISC_UNREAD_STATUSES = (
    DISC_UNREAD_MISSING,
    DISC_UNREAD_UNREADABLE,
    DISC_UNREAD_NOT_A_DISC,
    DISC_UNREAD_SECTOR_INVALID,
    DISC_UNREAD_BOOT_LINE_ABSENT,
)

EMULATOR_DUCKSTATION = "duckstation"
EMULATOR_SWANSTATION = "swanstation"
EMULATOR_BEETLE = "beetle-psx"
# The libretro cores whose firmware group a disc decides, by ``.so`` short name.
CORE_EMULATORS = {
    "swanstation": EMULATOR_SWANSTATION,
    "mednafen_psx": EMULATOR_BEETLE,
    "mednafen_psx_hw": EMULATOR_BEETLE,
}

PLAYLIST_SUFFIX = ".m3u"
# The images each emulator opens as a disc, by suffix and without regard to
# case: DuckStation's dispatch (src/util/cd_image.cpp:81-118 at 1f6de046b),
# SwanStation's (src/common/cd_image.cpp:38-70 at 4d309c0), and Beetle's
# declared extensions (libretro_options.h:20) — its image open reads every name
# but these as a cue sheet (mednafen/cdrom/CDAccess.cpp:48-62,
# CDAccess_Image.cpp:365-390), so a bare image is content it cannot open.
_DISC_SUFFIXES = {
    EMULATOR_DUCKSTATION: frozenset({".cue", ".bin", ".img", ".iso", ".chd", ".ecm", ".mds", ".pbp"}),
    EMULATOR_SWANSTATION: frozenset({".cue", ".bin", ".img", ".iso", ".chd", ".ecm", ".mds", ".pbp"}),
    EMULATOR_BEETLE: frozenset({".cue", ".toc", ".ccd", ".pbp", ".chd"}),
}
# The files each one boots without a disc when the launch names one directly —
# an executable or a sound file, whose region comes from a header atlas does not
# read (DuckStation system.cpp:777-786, :1609-1621; SwanStation system.cpp:195-208,
# :616-624; Beetle's ``exe`` extension). A playlist entry is opened as a disc,
# never as one of these.
_BOOT_SUFFIXES = {
    EMULATOR_DUCKSTATION: frozenset({".exe", ".psexe", ".ps-exe", ".psx", ".psf", ".minipsf"}),
    EMULATOR_SWANSTATION: frozenset({".exe", ".psexe", ".psf", ".minipsf"}),
    EMULATOR_BEETLE: frozenset({".exe"}),
}

# DuckStation's and SwanStation's licence strings (system.cpp:1084-1086 at
# 1f6de046b; :447-449 at 4d309c0), compared byte for byte from the sector's start.
_LICENSE_STRINGS: tuple[tuple[bytes, ConsoleRegion], ...] = (
    (b"          Licensed  by          Sony Computer Entertainment Amer  ica ", REGION_NTSC_U),
    (b"          Licensed  by          Sony Computer Entertainment Inc.", REGION_NTSC_J),
    (b"          Licensed  by          Sony Computer Entertainment Euro pe", REGION_PAL),
)
# DuckStation's prefixes, in its order (system.cpp:1059-1067); SwanStation's
# sets hold the same eleven words (system.cpp:430-435 at 4d309c0).
_SERIAL_PREFIXES: tuple[tuple[str, ConsoleRegion], ...] = (
    ("sces", REGION_PAL),
    ("sced", REGION_PAL),
    ("sles", REGION_PAL),
    ("sled", REGION_PAL),
    ("scps", REGION_NTSC_J),
    ("slps", REGION_NTSC_J),
    ("slpm", REGION_NTSC_J),
    ("sczs", REGION_NTSC_J),
    ("papx", REGION_NTSC_J),
    ("scus", REGION_NTSC_U),
    ("slus", REGION_NTSC_U),
)
_SERIAL_REGION: dict[str, ConsoleRegion] = dict(_SERIAL_PREFIXES)
# Beetle's third letter of a four-character id (libretro.cpp:1507-1530).
_BEETLE_ID_REGION: dict[int, ConsoleRegion] = {
    ord("E"): REGION_PAL,
    ord("U"): REGION_NTSC_U,
    ord("K"): REGION_NTSC_J,
    ord("B"): REGION_NTSC_J,
    ord("P"): REGION_NTSC_J,
}
# Beetle's licence words, in the order it tests them (libretro.cpp:1590-1616).
_BEETLE_LICENSE_WORDS: tuple[tuple[bytes, ConsoleRegion], ...] = (
    (b"america", REGION_NTSC_U),
    (b"europe", REGION_PAL),
    (b"japan", REGION_NTSC_J),
    (b"sonycomputerentertainmentinc.", REGION_NTSC_J),
)
# How many bytes ``filestream_gets`` hands Beetle as one line (libretro.cpp:4051, :4059).
_BEETLE_LINE = 2047
_C_SPACE = "".join(map(chr, psx_disc.C_SPACE))


@dataclass(frozen=True, slots=True)
class DiscRegionReading:
    """What one emulator decides about one content path's disc — a region and its source, or why there is none.

    ``region`` and ``source`` are set together and exactly when ``reason`` is
    ``None``. Otherwise ``reason`` is one of the three
    ``core-mode-unestablished`` reasons a statement about the disc rides on, and
    the fields beside it are that statement's data: ``disc`` is the image the
    region would be read from — the content path itself, or the playlist's first
    entry — ``status`` the word of ``disc-unread``, and ``extension`` the suffix
    a format or an open was refused on, lowered and without its dot, the way
    every other statement of an extension spells it. ``path`` is a file other
    than the disc that would have decided and could not be read — DuckStation's
    per-path properties file, under ``region-property-unread``.
    """

    region: ConsoleRegion | None = None
    source: RegionSource | None = None
    reason: str | None = None
    disc: str | None = None
    status: DiscUnreadStatus | None = None
    extension: str | None = None
    path: str | None = None

    def __post_init__(self) -> None:
        decided = self.region is not None and self.source is not None
        if decided == (self.reason is not None) or (self.region is None) != (self.source is None):
            raise ValueError("DiscRegionReading: a region and its source, or a reason — exactly one of the two")
        if self.region is not None and self.region not in DISC_REGIONS:
            raise ValueError(f"DiscRegionReading: region must be one of {DISC_REGIONS}, got {self.region!r}")
        if self.source is not None and self.source not in REGION_SOURCES:
            raise ValueError(f"DiscRegionReading: source must be one of {REGION_SOURCES}, got {self.source!r}")
        if self.status is not None and self.status not in DISC_UNREAD_STATUSES:
            raise ValueError(f"DiscRegionReading: status must be one of {DISC_UNREAD_STATUSES}, got {self.status!r}")


def decided(region: ConsoleRegion, source: RegionSource) -> DiscRegionReading:
    return DiscRegionReading(region=region, source=source)


# ── The three rules over an identity ──────────────────────────────────────


def _license_region(identity: psx_disc.DiscIdentity) -> ConsoleRegion | None:
    """``GetRegionFromSystemArea`` (system.cpp:1078-1098): the string the sector starts with, if it is one of three."""
    return next((region for text, region in _LICENSE_STRINGS if identity.license.startswith(text)), None)


def duckstation_region(identity: psx_disc.DiscIdentity) -> DiscRegionReading:
    """``GetRegionForImage`` (system.cpp:1100-1121) and the boot's fallback for a disc it cannot place (:1636-1649)."""
    region = _license_region(identity)
    if region is not None:
        return decided(region, REGION_SOURCE_LICENSE)
    executable = psx_disc.duckstation_executable(identity.system_cnf)
    if not executable or not identity.boot_found:
        # NonPS1, which the boot turns into NTSC-U (:812-823).
        return decided(REGION_NTSC_U, REGION_SOURCE_EMULATOR_DEFAULT)
    serial = executable[executable.rfind(b"\\") + 1 :].lower()
    by_serial: ConsoleRegion | None = next(
        (r for prefix, r in _SERIAL_PREFIXES if serial.startswith(prefix.encode())), None
    )
    if by_serial is None:
        return decided(REGION_NTSC_U, REGION_SOURCE_EMULATOR_DEFAULT)
    return decided(by_serial, REGION_SOURCE_SERIAL)


def _swanstation_code(system_cnf: bytes | None) -> bytes:
    """``GetGameCodeForImage(cdi, false)`` (system.cpp:228-256 at 4d309c0): the code, or empty where there is none.

    The value is the ``BOOT`` line's (the tokeniser at :283-330), cut after
    its last ``\\`` or, without one, its last ``:`` (``strip_subdirectories``,
    :334-348), and before its last ``;`` (:362-366).
    """
    value = None if system_cnf is None else psx_disc.boot_value(system_cnf)
    if value is None:
        return b""
    cut = value.rfind(b"\\")
    if cut < 0:
        cut = value.rfind(b":")
    code = value[cut + 1 :]
    semicolon = code.rfind(b";")
    if semicolon >= 0:
        code = code[:semicolon]
    return code.replace(b".", b"").replace(b"_", b"-").upper()


def swanstation_region(identity: psx_disc.DiscIdentity) -> DiscRegionReading:
    """``GetRegionForImage`` (system.cpp:462-473 at 4d309c0) and the boot's fallback for ``Other`` (:649-654)."""
    region = _license_region(identity)
    if region is not None:
        return decided(region, REGION_SOURCE_LICENSE)
    # GetRegionForCode (:418-438): the leading letters, lowered, as one word.
    letters = re.match(rb"[A-Za-z]*", _swanstation_code(identity.system_cnf))
    assert letters is not None  # an empty match is a match
    region = _SERIAL_REGION.get(letters.group().lower().decode("ascii"))
    if region is None:
        return decided(REGION_NTSC_U, REGION_SOURCE_EMULATOR_DEFAULT)
    return decided(region, REGION_SOURCE_SERIAL)


class _BootLineAbsent(Exception):
    """Beetle's block holds no ``BOOT``, and the core dereferences the search's null result (libretro.cpp:1477)."""


def _beetle_by_system_cnf(sector: bytes) -> ConsoleRegion | None:
    """``CalcDiscSCEx_BySYSTEMCNF``'s parse of the first block (libretro.cpp:1466-1531).

    A region, or ``None`` where the block names none. Raises
    :class:`_BootLineAbsent` where it holds no ``BOOT`` at all, which no
    reading here survives any more than the core does.
    """
    text = sector.split(b"\x00", 1)[0]
    at = text.find(b"BOOT")
    if at < 0:
        raise _BootLineAbsent
    rest = text[at + 4 :].lstrip(b" \t")
    if rest[:1] != b"=":
        return None
    rest = rest[1:].lstrip(b" \t")
    if rest[:7].lower() != b"cdrom:\\":
        return None
    name = rest[7:]
    # strchr for '_', then '.', then ';' — each cut on what the last left (:1495-1500).
    for stop in (b"_", b".", b";"):
        name = name.split(stop, 1)[0]
    if len(name) != 4 or name[0] != ord("S") or name[1] not in b"CLI":
        return None
    return _BEETLE_ID_REGION.get(name[2])


def beetle_region(identity: psx_disc.DiscIdentity) -> DiscRegionReading:
    """``CalcDiscSCEx`` for the first disc (libretro.cpp:1560-1640): ``SYSTEM.CNF`` first, the licence words second."""
    if not identity.edc_valid:
        return DiscRegionReading(reason=REASON_DISC_UNREAD, status=DISC_UNREAD_SECTOR_INVALID)
    if identity.system_cnf_sector is not None:
        try:
            found = _beetle_by_system_cnf(identity.system_cnf_sector)
        except _BootLineAbsent:
            return DiscRegionReading(reason=REASON_DISC_UNREAD, status=DISC_UNREAD_BOOT_LINE_ABSENT)
        if found is not None:
            return decided(found, REGION_SOURCE_SERIAL)
    # Printable bytes of the first 0x48, lowered, as one string (:1579-1587).
    folded = bytes(byte for byte in identity.license if 0x20 < byte < 0x80).lower()
    if b"licensedby" in folded:
        by_license: ConsoleRegion | None = next((r for word, r in _BEETLE_LICENSE_WORDS if word in folded), None)
        if by_license is not None:
            return decided(by_license, REGION_SOURCE_LICENSE)
    return decided(REGION_NTSC_U, REGION_SOURCE_EMULATOR_DEFAULT)


_RULES = {
    EMULATOR_DUCKSTATION: duckstation_region,
    EMULATOR_SWANSTATION: swanstation_region,
    EMULATOR_BEETLE: beetle_region,
}


# ── Playlists ─────────────────────────────────────────────────────────────


# The most a playlist may hold for atlas to read it. The emulators read any
# length; a playlist over this is answered as one atlas did not read.
_PLAYLIST_LIMIT = 4 * 1024 * 1024


def _read_playlist(machine: Machine, playlist: str) -> ReadResult:
    """A playlist's text exactly as written: no newline translation, because both readers split on ``\\n`` alone.

    A machine that cannot bound its read answers through :meth:`read_text`
    after a size check, the way :mod:`atlas.installations` falls back.
    """
    if isinstance(machine, BoundedReadingMachine):
        read = machine.read_text_within(playlist, _PLAYLIST_LIMIT)
        return ReadResult(READ_UNREADABLE) if read is None else read
    size = machine.file_size(playlist)
    if size is not None and size > _PLAYLIST_LIMIT:
        return ReadResult(READ_UNREADABLE)
    return machine.read_text(playlist)


def _beside(playlist: str, entry: str) -> str:
    """An entry joined onto its playlist's directory unless it is absolute (``BuildRelativePath``, ``MDFN_EvalFIP``)."""
    return entry if entry.startswith("/") else os.path.join(os.path.dirname(playlist), entry)


def duckstation_playlist_entries(text: str, playlist: str) -> list[str]:
    """``CDImageM3u::Open``'s entries (cd_image_m3u.cpp:76-112 at 1f6de046b; :64-100 at 4d309c0).

    One line per ``\\n`` — ``std::getline``'s only delimiter, so a ``\\r``
    stays inside the line and is trimmed only where it trails; leading space skipped, then a ``#`` or nothing left
    skips the line; trailing space trimmed down to the first character, and a
    line whose start and end then meet — one character — skipped too.
    DuckStation also folds repeated ``/`` (``Path::ToNativePath``), which the
    machine's path walk makes no difference to.
    """
    entries: list[str] = []
    for line in text.split("\n"):
        start = len(line) - len(line.lstrip(_C_SPACE))
        if start == len(line) or line[start] == "#":
            continue
        end = len(line) - 1
        while line[end] in _C_SPACE and end > start:
            end -= 1
        if start == end:
            continue
        entries.append(_beside(playlist, line[start : end + 1]))
    return entries


def _beetle_lines(text: str) -> list[str]:
    """``filestream_gets`` into a 2048-byte buffer: a line ends at ``\\n`` or after 2047 bytes, whichever is first.

    Only ``\\n`` ends a line, as ``fgets`` ends one; a ``\\r`` stays inside it.
    """
    pieces = text.split("\n")
    lines: list[str] = []
    for raw in [piece + "\n" for piece in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else []):
        encoded = raw.encode("utf-8", "surrogateescape")
        lines.extend(
            encoded[i : i + _BEETLE_LINE].decode("utf-8", "surrogateescape")
            for i in range(0, len(encoded), _BEETLE_LINE)
        )
    return lines


class BeetleCycle(Exception):
    """A playlist that reaches itself again through another — recursion the core never returns from."""


def _beetle_entries(machine: Machine, playlist: str, stack: tuple[str, ...]) -> list[str] | None:
    """``ReadM3U`` (libretro.cpp:4048-4090): the flattened entries, or ``None`` where the top file did not read.

    A nested playlist that will not open contributes nothing (:4054-4055), one
    that names itself ends the read of the file naming it (:4074-4078), and one
    reached again through another is a loop the core recurses into without end.
    """
    read = _read_playlist(machine, playlist)
    if read.status != READ_OK:
        return None if not stack else []
    assert read.text is not None
    entries: list[str] = []
    for raw in _beetle_lines(read.text):
        if raw.startswith("#"):
            continue
        line = raw.rstrip(_C_SPACE)
        if not line:
            continue
        entry = _beside(playlist, line)
        if not entry.endswith(PLAYLIST_SUFFIX):
            entries.append(entry)
            continue
        if entry == playlist:
            break
        if entry in stack:
            raise BeetleCycle(entry)
        entries.extend(_beetle_entries(machine, entry, (*stack, playlist)) or [])
    return entries


def beetle_playlist_entries(machine: Machine, playlist: str) -> list[str] | None:
    """Beetle PSX's flattened entries of *playlist*, ``None`` where it did not read, or :class:`BeetleCycle`."""
    return _beetle_entries(machine, playlist, ())


# ── One emulator, one content path ────────────────────────────────────────


def _extension(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _stated_extension(path: str) -> str:
    """The suffix a statement carries: lowered, without its dot, empty where the name has none."""
    return _extension(path).removeprefix(".")


def _unopenable(path: str) -> DiscRegionReading:
    return DiscRegionReading(reason=REASON_CONTENT_UNOPENABLE, disc=path, extension=_stated_extension(path))


def _format_unread(path: str) -> DiscRegionReading:
    return DiscRegionReading(reason=REASON_DISC_FORMAT_UNREAD, disc=path, extension=_stated_extension(path))


def _playlist_entries(machine: Machine, emulator: str, playlist: str) -> list[str] | DiscRegionReading:
    """The entries *emulator* reads out of *playlist*, or why it reads none."""
    read = _read_playlist(machine, playlist)
    if read.status == READ_MISSING:
        return DiscRegionReading(reason=REASON_DISC_UNREAD, disc=playlist, status=DISC_UNREAD_MISSING)
    if read.status != READ_OK or read.text is None:
        return DiscRegionReading(reason=REASON_DISC_UNREAD, disc=playlist, status=DISC_UNREAD_UNREADABLE)
    if emulator != EMULATOR_BEETLE:
        return duckstation_playlist_entries(read.text, playlist)
    try:
        return _beetle_entries(machine, playlist, ()) or []
    except BeetleCycle:
        return _unopenable(playlist)


def _first_disc(
    machine: Machine, emulator: str, content_path: str, seen: tuple[str, ...] = ()
) -> DiscRegionReading | str:
    """The image the emulator decides from — the path itself, or its playlist's first entry — or why there is none.

    DuckStation and SwanStation open a playlist's entry through the same
    dispatch as any content, so a nested playlist is followed to its own first
    entry; Beetle has already flattened its nesting. A playlist reached again on
    that walk is a recursion the emulator never returns from.
    """
    if _extension(content_path) != PLAYLIST_SUFFIX:
        return content_path
    if content_path in seen:
        return _unopenable(content_path)
    entries = _playlist_entries(machine, emulator, content_path)
    if isinstance(entries, DiscRegionReading):
        return entries
    if not entries:
        return _unopenable(content_path)
    if emulator == EMULATOR_BEETLE:
        return entries[0]
    return _first_disc(machine, emulator, entries[0], (*seen, content_path))


_SEAM_WORDS: dict[str, DiscUnreadStatus] = {
    DISC_MISSING: DISC_UNREAD_MISSING,
    DISC_UNREADABLE: DISC_UNREAD_UNREADABLE,
    DISC_NOT_A_DISC: DISC_UNREAD_NOT_A_DISC,
}


def read_disc_region(machine: Machine, emulator: str, content_path: str) -> DiscRegionReading:
    """The region *emulator*'s own rule decides for the content at *content_path*, or the statement of why none.

    The playlist first, by the emulator's own reader; then whether the emulator
    opens the image at all (``content-unopenable`` where it does not); then
    whether atlas reads that image (``disc-format-unread`` where it does not);
    then the seam's read of the disc, and the emulator's rule over it. Nothing
    configurable is consulted here — a setting that pins the region is the
    caller's to read first, because only the caller knows where that emulator's
    settings live.
    """
    first = _first_disc(machine, emulator, content_path)
    if isinstance(first, DiscRegionReading):
        return first
    extension = _extension(first)
    if first == content_path and extension in _BOOT_SUFFIXES[emulator]:
        return _format_unread(first)
    if extension not in _DISC_SUFFIXES[emulator]:
        return _unopenable(first)
    if not machine_reads(first):
        return _format_unread(first)
    result = machine.read_disc_identity(first)
    if result.status == DISC_FORMAT_NOT_READ:
        return _format_unread(first)
    if result.status != DISC_OK:
        return DiscRegionReading(reason=REASON_DISC_UNREAD, disc=first, status=_SEAM_WORDS[result.status])
    assert result.identity is not None
    reading = _RULES[emulator](result.identity)
    return reading if reading.reason is None else DiscRegionReading(
        reason=reading.reason, disc=first, status=reading.status
    )


def machine_reads(path: str) -> bool:
    """Is *path* an image the disc read opens — a cue sheet or a bare image, by suffix — for every emulator but Beetle?

    Beetle opens no bare image (its declared extensions name none), so for it
    only a cue sheet reaches the read; :func:`read_disc_region` refuses the
    others before asking.
    """
    return _extension(path) in (psx_disc.CUE_SUFFIX, *psx_disc.BARE_IMAGE_SUFFIXES)


# ── DuckStation's two region settings ─────────────────────────────────────

# ``[Console] Region``'s names (settings.cpp:943-948 at 1f6de046b), compared
# without regard to case (:956-968); ``Auto`` and a value it does not parse —
# which falls back to the default ``Auto`` (:140-144, settings.h:473) — pin none.
_DUCKSTATION_CONSOLE_REGIONS: dict[str, ConsoleRegion] = {"ntsc-j": REGION_NTSC_J, "ntsc-u": REGION_NTSC_U, "pal": REGION_PAL}
# A disc region's names (settings.cpp:981-983), the custom property's
# vocabulary (game_list.cpp:636-648); ``Other`` and ``Non-PS1`` boot as NTSC-U
# (system.cpp:812-823, :1638-1647).
_DUCKSTATION_DISC_REGIONS: dict[str, ConsoleRegion] = {
    "ntsc-j": REGION_NTSC_J,
    "ntsc-u": REGION_NTSC_U,
    "pal": REGION_PAL,
    "other": REGION_NTSC_U,
    "non-ps1": REGION_NTSC_U,
}


def duckstation_console_region(value: str | None) -> ConsoleRegion | None:
    """The region ``[Console] Region`` pins, or ``None`` where it pins none (``Auto``, unset, or unparsed)."""
    return None if value is None else _DUCKSTATION_CONSOLE_REGIONS.get(value.strip().lower())


def duckstation_custom_region(value: str | None) -> ConsoleRegion | None:
    """The console region a custom ``Region`` property boots, or ``None`` where it names no disc region."""
    return None if value is None else _DUCKSTATION_DISC_REGIONS.get(value.strip().lower())


def custom_property(text: str, path: str, key: str) -> str | None:
    """The value ``custom_properties.ini`` holds for *key* in the section named *path*, as SimpleIni reads it.

    DuckStation keeps the file through ``INISettingsInterface``, a SimpleIni
    reader whose section and key comparison ignores ASCII case on Linux and
    whose last duplicate wins (the chain :func:`atlas.qt_ini.simpleini_value`
    cites). A section name is a path written as it stands — this file is not a
    Qt settings file, so no ``%XX`` is undone — up to the first ``]``, as
    SimpleIni ends it (``dep/simpleini/include/SimpleIni.h:1643-1679`` at
    RetroDECK/Duckstation@1f6de046b), so a content path holding a ``]`` is never
    the section DuckStation reads; names, keys and values are trimmed of
    surrounding space. ``None`` where the file holds no such entry.
    """
    wanted = (qt_ini.ascii_locase(path), qt_ini.ascii_locase(key))
    section = ""
    found: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in ";#":
            continue
        if line.startswith("["):
            # The name ends at the FIRST ``]``; a line with none is skipped
            # whole and leaves the section as it was (SimpleIni.h:1643-1679).
            if "]" in line:
                section = line[1 : line.index("]")].strip()
            continue
        name, separator, value = line.partition("=")
        if separator and (qt_ini.ascii_locase(section), qt_ini.ascii_locase(name.strip())) == wanted:
            found = value.strip()
    return found
