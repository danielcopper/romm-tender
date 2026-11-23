"""The files a launched disc file opens — each emulator's own open, read step for step (issue #716).

A disc's ``.cue`` and its ``.bin`` tracks, and an ``.m3u`` listing several
discs, are all files an entry may be handed. What tells them apart is what
the emulator does with each, and that is what this module reads: the files the emulator opens through the
launched file, in the order it opens them, each either **named** by the text
of another file (a cue sheet's ``FILE``, a playlist's line) or **derived from a
name** (a ``.sbi`` beside the image, the ``<cue stem>.bin`` a cue's missing
first file is looked for as). Every lookup goes to the filesystem the emulator
opens it on, exactly as spelled — on Linux's own filesystems that is case
sensitive, as ``fopen`` is, and atlas asks the same filesystem.

Four readings are served. Every citation is at the build atlas pins for
that emulator elsewhere — DuckStation at RetroDECK/Duckstation@1f6de046b,
SwanStation at libretro/swanstation@4d309c0, Beetle PSX at
libretro/beetle-psx-libretro@d6383bf, Beetle Saturn at ccba526, Beetle PCE at
9a301c0, Beetle PC-FX at dd04cef, Flycast at flyinghead/flycast@1dac369 and
Genesis Plus GX at libretro/Genesis-Plus-GX@46a5521 — save Beetle PCE Fast,
which atlas pins nowhere else: its citations are at 7e9b257, pinned here
first. RetroArch's part, where a core's reading depends on it, is cited at
libretro/RetroArch@a79435a.

**DuckStation and SwanStation** share one open (``CDImage::Open``,
DuckStation ``src/util/cd_image.cpp:53-138``, SwanStation
``src/common/cd_image.cpp:18-72``): the suffix after the last ``.`` of the
whole path decides, ignoring case. A ``.cue`` is parsed whole first in the
grammar :mod:`atlas.psx_disc` ports (``cue_parser.cpp``, the same in both,
``BINARY`` the only ``FILE`` type it takes), including the order check of
``SetTrackLengths`` (DuckStation :442-470, SwanStation :435-463); then its
files are opened per track, track 1 upward until a number is missing, each
file once, beside the cue unless absolute (``cd_image_cue.cpp:83-128`` /
:69-118). A first file that will not open is looked for again as the cue's
own path with its extension replaced by ``bin`` (:105-116 / :91-102). A bare
``.bin``, ``.img`` or ``.iso`` is opened as itself; an ``.mds`` opens the
``.mdf`` of its own name (``cd_image_mds.cpp:90`` / :78) after reading its own
descriptor, which atlas does not read. Each of these then looks for the
``.sbi`` of its own name — DuckStation the ``.lsd`` too where that does not
load (``cd_subchannel_replacement.cpp:133-142``; SwanStation :91-94). An
``.m3u`` opens its first entry at launch and every other only when the
player switches to it (``cd_image_m3u.cpp:62-160`` / :49-144), so a nested
playlist contributes its first entry alone. Where the setting that loads
patches is on, a ``.ppf`` of the image's name is looked for: DuckStation for
every image it opens, the playlist itself included, where a patch it cannot
apply fails the open (``cd_image.cpp:120-135``, ``[CDROM] LoadImagePatches``
read once before the boot, ``system.cpp:1625``); SwanStation for the launched
path alone, falling back to the unpatched image (``system.cpp:566-588``,
the core option ``swanstation_CDROM_LoadImagePatches``).

**The mednafen cores** — Beetle PSX and its hardware build, Beetle Saturn,
Beetle PCE, PCE Fast and PC-FX — open a cue sheet in ``CDAccess_Image``,
which opens every ``FILE`` at its line, beside the cue unless absolute, and
reads the track type that follows (Beetle PSX ``CDAccess_Image.cpp:617-670``;
the four newer copies, e.g. Saturn :586-628): ``BINARY`` and the raw audio
types are taken as they are, the types handed to an audio reader are decided
by the file's bytes (the Ogg Vorbis reader alone, ``audioreader.cpp:167-170``;
``CDAFReader.cpp:45-52`` without ``HAVE_MPC``), and any other type, MP3 among
them, ends the open. After the sheet the ``.sbi`` of the cue's name is looked
for, its extension cased like the cue's (:899-921; Saturn :864-881). A
``.ccd`` opens the ``.img`` and ``.sub`` of its own name, cased the same way,
after its own sections (``CDAccess_CCD.cpp:79-288``). An ``.m3u`` is read by
``ReadM3U`` (:mod:`atlas.psx_region`), every disc it names opened at load.
Beetle PSX loads the ``.toc`` of the content's name in place of the content
where one is there (:func:`atlas.psx_region.beetle_toc`).

**Flycast** reads its own three formats (``retro_load_game``,
``libretro.cpp:2100-2307``). A ``.gdi`` (``gdi.cpp:6-116``) is a stream of
whitespace-separated fields: a track count, then per track its number, start,
control, sector size, a name quoted or one token, and an offset; a track's
file is opened only where its sector size is not 0, and a sheet of fewer than
three tracks fails after its files opened. A ``.cue`` (``cue.cpp:46-250``) is
read a line at a time: the ``FILE`` type is never checked, the track's file is
opened at its ``INDEX 01`` and the track mode checked after it, and a sheet
without a ``REM SESSION`` line needs three tracks. Either sheet's file is
joined onto its directory with a ``/`` whatever the name, an absolute one
too, and a sheet past 8192 bytes (``.gdi``) or 65535 (``.cue``) is refused by
its size. A parser that reads a variable it never set — a ``.gdi`` declaring
more tracks than it holds, a line that ends where a field is read — is
undefined from there; a quoted name no quote ends loops forever. An ``.m3u``
is taken as a playlist only where the file's name, cut to 127 bytes, ends in
``.m3u`` or ``.M3U`` (``libretro.cpp:2117``, :2159-2187); ``read_m3u``
(:3740-3792) skips a ``#`` in the first column, ends a line at its first
``\r`` or ``\n``, drops one leading and one trailing ``"``, and joins a
relative line onto the content's directory cut to 1023 bytes. Its first disc
is opened at launch through ``Emulator::loadGame`` (``emulator.cpp:547-621``),
which takes an arcade or executable name as no disc; every other one only on
a disc switch, through ``OpenDisc`` alone (``common.cpp:86-97``, :366-369).

**Genesis Plus GX** opens everything through ``cdd_load``
(``cdd.c:390-1304``). A path whose last three characters are ``cue`` or
``CUE`` is a cue sheet, read in 127-byte pieces: only spaces lead a line, a
``FILE`` name is written after the last separator of the path built before it
and cut where the whole reaches 255 bytes, a file that will not open ends the
parse, and a type other than ``BINARY`` or ``MOTOROLA`` is decided by the
file's bytes; a track ends at an ``INDEX 01`` line, 99 at most. The ``.sub``
it looks for is the last path built with its last four bytes replaced. Any
other file is a CD data track or a cartridge by its first bytes, and the
``.cue`` of its name is looked for either way (:586-660); where no track
mounts, ``load_rom`` loads the file it was handed as a cartridge
(``loadrom.c:579-597``), up to 10 MiB. Without that ``.cue``, a file a
cartridge load takes therefore launches whatever its bytes are; a disc
switch runs ``cdd_load`` alone (``libretro.c:2817``), so there a file that
mounts no track fails the switch. A first track with no ``FILE`` opened
for it crashes the core, which reads or seeks on a stream it never opened
(cdd.c:826-836, :949-951). An ``.m3u`` — by any case, since RetroArch hands
the extension over lowercased — names at most four discs
(``libretro.c:2762``, :3563-3633), each joined onto the content's directory
with a ``/``, a blank line too; the first is loaded at launch and the
others on a disc switch.

The cores part on what happens when the open fails, and that is what the
verdict follows (D4 and D5 of #716). Beetle PSX checks each ``FILE`` open
(``CDAccess_Image.cpp:642-643``) and the result of the sheet for a directly launched file
(``libretro.cpp:4172-4174``), so there it stops; a disc of a playlist is
loaded whether it opened or not (:4110-4123). The four newer cores never
fail a ``FileStream`` open (``FileStream.cpp``, e.g. Saturn :28-31) and discard what the
sheet's read returns (e.g. Saturn ``CDAccess_Image.cpp:911-916``), so they go
on without whatever is missing or refused. DuckStation and SwanStation stop
where the disc opened at launch fails, and only there. Beetle PCE and PCE
Fast set the result of a load only in their single-disc branch, so every
``.m3u`` fails after its discs were opened (PCE ``libretro.cpp:685-722``,
PCE Fast :1420-1458). Flycast stops where the disc it opens at launch fails,
and goes on where a disc switch fails. Genesis Plus GX stops where no track of
the disc loaded at launch mounts — its first file missing, a sheet with no
track, a file that is no cue sheet and no CD — since it then loads that file
as a cartridge instead, and where a sheet it parses at launch crashes it, the
``.cue`` of a cartridge's name included; a later track missing leaves the disc
mounted with the tracks before it, and a disc switch that fails leaves the
launch as it was.

What atlas does not read is stated rather than guessed: a file whose bytes
decide (an audio track a reader opens, an ``.mds`` or ``.ccd`` descriptor, a
patch DuckStation applies, an ``.sbi`` the newer cores check), a format this
cut does not follow (``.chd``, ``.pbp``, ``.ecm``, ``.toc``), a track's length
against its file's size, and a setting that could not be read. A path the
system cannot open at all — 4096 bytes or more, Linux's limit whatever the
filesystem — is what the emulator's open fails on: it is stated once as such,
cut to :data:`MAX_SHOWN` characters, and never looked up or listed. A long
name inside a shorter path goes to the real lookup, since how long a name may
be is each filesystem's own.

The walk is bounded, because the content is untrusted: :data:`MAX_LISTED`
distinct files, :data:`MAX_NESTING` playlists deep, :data:`MAX_TRACKS` tracks
in one sheet, :data:`MAX_STEPS` steps, :data:`MAX_READ_BYTES` bytes read and
:data:`MAX_OUTPUT` characters of answer. Every file opened or looked for is a
step, again on every visit, since the emulators open a playlist or a disc
named twice twice; so is every line of a playlist read and every line of a
cue sheet parsed or walked, and every 1023-byte piece DuckStation's ``fgets``
cuts a longer sheet line into, counted from above. A mednafen core's
playlists and sheets are read afresh on every visit, as the core reads them;
DuckStation and SwanStation read a nested playlist or a sheet once per
spelling of its path and reading, and keep only its first entry or its parsed
tracks. Every read is charged to the byte bound whatever it returned — the
text, or the file's size up to its bound and the byte past it — and the bound
is checked after each read, so a reading stops at most one read past it. A
cue sheet's bound is :data:`atlas.psx_disc.MAX_CUE_BYTES`, a playlist's
:data:`atlas.psx_region.PLAYLIST_LIMIT`. Every listed path and the file naming
it, every disc path a mednafen playlist names while its list is read, every
playlist entry, sheet track and refused sheet's file and type DuckStation and
SwanStation keep, and every caveat and source is charged to the output bound,
and a caveat is stated once per code, file and effect — remembered by its
path, or past :data:`MAX_SHOWN` characters by the shown form and a digest,
which is charged with it. So what one reading holds is bounded three ways:
the texts on its current path of reads, at most :data:`MAX_READ_BYTES` of
file bytes together (up to four times that in memory for text outside
Latin-1); its answer with everything it remembers to build it, at most
:data:`MAX_OUTPUT` characters; and one parse at a time of at most
:data:`MAX_STEPS` lines.

A NUL cannot reach a path: both emulators hand a name on as a C string, so a
playlist entry ends at its first NUL (:mod:`atlas.psx_region`) and a cue
sheet's line already does.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Final, Literal

from . import psx_disc, psx_region
from .machine import KIND_FILE, KIND_MISSING, READ_OK, BoundedReadingMachine, Machine, ReadResult
from .opens_codes import (
    CAVEAT_OPENS_BOUND_EXCEEDED,
    CAVEAT_OPENS_CONTENT_UNREAD,
    CAVEAT_OPENS_ENTRY_IGNORED,
    CAVEAT_OPENS_FILE_MISSING,
    CAVEAT_OPENS_FILE_UNREAD,
    CAVEAT_OPENS_FORMAT_REFUSED,
    CAVEAT_OPENS_FORMAT_UNREAD,
    CAVEAT_OPENS_PATH_TOO_LONG,
    CAVEAT_OPENS_PLAYLIST_REFUSED,
    CAVEAT_OPENS_SETTING_UNREAD,
    CAVEAT_OPENS_SHEET_REFUSED,
    CAVEAT_OPENS_TRACK_REFUSED,
    EFFECT_GOES_ON,
    EFFECT_STOPS,
    EFFECT_UNESTABLISHED,
    OpensEffect,
)
from .placement import Caveat

OpenedHow = Literal["named", "name-derived"]
HOW_NAMED: OpenedHow = "named"
"""The text of another file names it: a cue sheet's ``FILE`` line or a playlist's entry."""
HOW_NAME_DERIVED: OpenedHow = "name-derived"
"""The emulator derives it from another file's own name: a ``.sbi`` beside the image, the ``.mdf`` of an ``.mds``."""
# How an opened file came to be opened, in the alias's own order.
OPENED_HOWS = (HOW_NAMED, HOW_NAME_DERIVED)


@dataclass(frozen=True, slots=True)
class Opened:
    """One file the emulator opens through the launched file, looks for and does not find, or stops its open at.

    A client adopting, checking or cleaning up an install sees every file the
    emulator touches: ``present`` says whether it is there, so a companion the
    emulator looks for and goes on without is listed as well as a track it
    needs.
    """

    path: str
    """The path the emulator opens, in its own spelling — joined onto the naming file's directory where the name is
    relative, with no link resolved and nothing normalised.
    """
    how: OpenedHow
    """Whether the text of another file names it (``named``) or the emulator derives it from a file's own name
    (``name-derived``) — one of :data:`OPENED_HOWS`.
    """
    by: str
    """The file that names it, or whose own name it is derived from."""
    present: bool | None
    """Whether a file is there: ``true`` or ``false``, and ``null`` where that cannot be told — a path whose ``stat``
    fails, or a directory where a file is named.
    """

    def __post_init__(self) -> None:
        if self.how not in OPENED_HOWS:
            raise ValueError(f"Opened: how must be one of {OPENED_HOWS}, got {self.how!r}")


# What a reading established about one entry's take on one path.
Outcome = Literal["accepts", "refuses", "unestablished"]
ACCEPTS: Outcome = "accepts"
REFUSES: Outcome = "refuses"
UNESTABLISHED: Outcome = "unestablished"

# atlas's own bounds on a walk the content drives: the files one reading lists,
# how deep playlists may nest, the tracks one sheet may declare (a disc holds
# at most 99, and DuckStation's parser counts none — cue_parser.cpp:238-261
# checks a number, not how many), and the steps one reading takes — each far
# past a real game's set.
MAX_LISTED: Final = 4096
MAX_NESTING: Final = 16
MAX_TRACKS: Final = 1024
MAX_STEPS: Final = 1 << 16
MAX_READ_BYTES: Final = 32 << 20
# What one reading may hold for its answer, counted in characters: every listed
# path and the file naming it, every disc path a mednafen playlist names (held
# until its whole list is read), every playlist entry, sheet track and refused
# sheet's file and type DuckStation and SwanStation keep, every caveat's
# message and data, every source.
MAX_OUTPUT: Final = 1 << 20
# How much of a path a caveat states; a longer one is cut there and marked.
MAX_SHOWN: Final = 512
# Linux's limit on a path an open reaches, the kernel's own whatever the
# filesystem: PATH_MAX counts the terminating NUL, so 4095 bytes at most. A
# name's own limit belongs to each filesystem and is left to the real lookup.
_PATH_MAX = 4096
# What fail() says of a path the system cannot open.
_TOO_LONG = "opens {file}, a path longer than the system opens (4095 bytes in all), and the open fails on it"
# What the readers say of a file missing, of one whose presence cannot be
# told, and of one that could not be read.
_MISSING = "opens {file}, which is not there"
_UNTOLD = "whether {file} can be opened cannot be told"
_UNREAD = "{file} could not be read"
# What the readers say of a companion whose presence cannot be told, of a
# format they do not follow, and of a cue sheet that did not read as one.
_UNTOLD_THERE = "whether {file} is there cannot be told"
_NOT_FOLLOWED = "{file} is a format this reading does not follow"
_NOT_A_SHEET = "{file} could not be read as a cue sheet"


@dataclass(frozen=True, slots=True)
class OpensReading:
    """One entry's reading of one launched file: its take, what it opens, and why.

    ``opens`` is ``None`` only where the launched file itself could not be
    read; otherwise it lists what the emulator got to, and on a refusal the
    file it stopped at.
    """

    outcome: Outcome
    opens: tuple[Opened, ...] | None
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


@dataclass(frozen=True, slots=True)
class PatchSetting:
    """Whether the emulator's setting loads ``.ppf`` patches — ``None`` where it could not be read.

    ``setting`` is the setting's own spelling, which a caveat names;
    ``sources`` and ``caveats`` are what reading it said and cost.
    """

    loads: bool | None
    setting: str
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


@dataclass(frozen=True, slots=True)
class DuckProfile:
    """DuckStation's or SwanStation's open: the shared one, and the three places they part.

    ``patch_failure_fails_open`` is what a ``.ppf`` it cannot apply does:
    DuckStation's open fails (``cd_image.cpp:120-135``), SwanStation reopens
    the image unpatched (``system.cpp:566-580`` at 4d309c0).
    """

    name: str
    looks_for_lsd: bool
    patches_every_image: bool
    patch_failure_fails_open: bool


DUCKSTATION: Final = DuckProfile(
    "DuckStation 1f6de046b", looks_for_lsd=True, patches_every_image=True, patch_failure_fails_open=True
)
SWANSTATION: Final = DuckProfile(
    "SwanStation 4d309c0", looks_for_lsd=False, patches_every_image=False, patch_failure_fails_open=False
)

# What a mednafen core does with an ``.m3u`` it is launched with.
PlaylistStance = Literal["read", "refused", "unread"]
PLAYLIST_READ: PlaylistStance = "read"
PLAYLIST_REFUSED: PlaylistStance = "refused"
PLAYLIST_UNREAD: PlaylistStance = "unread"

# The cue track modes each grammar takes (Beetle PSX CDAccess_Image.cpp:102-113;
# the newer copies add CD-i, e.g. Saturn :98-108).
_PSX_FORMATS = ("AUDIO", "MODE1/2048", "MODE1/2352", "MODE2/2336", "MODE2/2048", "MODE2/2324", "MODE2/2352")
_NEWER_FORMATS = (*_PSX_FORMATS, "CDI/2352")


@dataclass(frozen=True, slots=True)
class MednafenProfile:
    """One mednafen core's open of a cue sheet, a ``.ccd`` and an ``.m3u``.

    ``newer`` is the grammar of the four later copies of ``CDAccess_Image``;
    ``stops`` says the core stops a directly launched load where the sheet's
    read fails (Beetle PSX alone). ``raw_types`` are the ``FILE`` types taken
    as they are, ``reader_types`` the ones handed to an audio reader, which
    decides by the file's bytes; any other type ends the sheet.
    """

    name: str
    newer: bool
    stops: bool
    raw_types: frozenset[str]
    reader_types: frozenset[str]
    loads_toc_beside: bool = False
    playlist: PlaylistStance = PLAYLIST_READ
    checks_sbi: bool = False
    loader: str = ""


BEETLE_PSX: Final = MednafenProfile(
    "Beetle PSX d6383bf",
    newer=False,
    stops=True,
    raw_types=frozenset({"BINARY"}),
    reader_types=frozenset({"OGG", "VORBIS", "WAVE", "WAV", "PCM", "MPC", "MP+"}),
    loads_toc_beside=True,
)
BEETLE_SATURN: Final = MednafenProfile(
    "Beetle Saturn ccba526",
    newer=True,
    stops=False,
    raw_types=frozenset({"BINARY", "WAVE", "WAV", "PCM"}),
    reader_types=frozenset({"OGG", "VORBIS", "MPC", "MP+"}),
    checks_sbi=True,
)
_PCE_RAW = frozenset({"BINARY", "WAVE", "WAV"})
_PCE_READER = frozenset({"OGG", "VORBIS", "PCM", "MPC", "MP+"})
# The one switch for Beetle PCE's and PCE Fast's ``.m3u`` (D5 of #716).
PCE_PLAYLIST: Final[PlaylistStance] = PLAYLIST_REFUSED
BEETLE_PCE: Final = MednafenProfile(
    "Beetle PCE 9a301c0",
    newer=True,
    stops=False,
    raw_types=_PCE_RAW,
    reader_types=_PCE_READER,
    playlist=PCE_PLAYLIST,
    checks_sbi=True,
    loader="MDFNI_LoadCD, libretro.cpp:685-722",
)
BEETLE_PCE_FAST: Final = MednafenProfile(
    "Beetle PCE Fast 7e9b257",
    newer=True,
    stops=False,
    raw_types=_PCE_RAW,
    reader_types=_PCE_READER,
    playlist=PCE_PLAYLIST,
    checks_sbi=True,
    loader="MDFNI_LoadCD, libretro.cpp:1420-1458",
)
BEETLE_PCFX: Final = MednafenProfile(
    "Beetle PC-FX dd04cef",
    newer=True,
    stops=False,
    raw_types=_PCE_RAW,
    reader_types=_PCE_READER,
    checks_sbi=True,
)



@dataclass(frozen=True, slots=True)
class FlycastProfile:
    """Flycast's libretro open of a ``.gdi``, a ``.cue`` and an ``.m3u``."""

    name: str


@dataclass(frozen=True, slots=True)
class GpgxProfile:
    """Genesis Plus GX's libretro open of a ``.cue``, an ``.m3u`` and a bare image."""

    name: str


FLYCAST: Final = FlycastProfile("Flycast 1dac369")
GENESIS_PLUS_GX: Final = GpgxProfile("Genesis Plus GX 46a5521")

Profile = DuckProfile | MednafenProfile | FlycastProfile | GpgxProfile

# The libretro cores read here, by their ``.so`` file name.
CORE_PROFILES: Final[dict[str, Profile]] = {
    "swanstation_libretro.so": SWANSTATION,
    "mednafen_psx_libretro.so": BEETLE_PSX,
    "mednafen_psx_hw_libretro.so": BEETLE_PSX,
    "mednafen_saturn_libretro.so": BEETLE_SATURN,
    "mednafen_pce_libretro.so": BEETLE_PCE,
    "mednafen_pce_fast_libretro.so": BEETLE_PCE_FAST,
    "mednafen_pcfx_libretro.so": BEETLE_PCFX,
    "flycast_libretro.so": FLYCAST,
    "genesis_plus_gx_libretro.so": GENESIS_PLUS_GX,
}

PatchReader = Callable[[], PatchSetting]


@dataclass(frozen=True, slots=True)
class _Mode:
    """How a failure counts: whether the emulator stops there, and whether doubt about it decides the take."""

    stops: bool
    decisive: bool
    goes_on: str = ""


class _Bounded(Exception):
    """The walk reached one of atlas's bounds at *path*."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


def _too_long(path: str) -> bool:
    """A path no open reaches: :data:`_PATH_MAX` bytes or more (ENAMETOOLONG).

    Its length in characters decides first, since a character is at least a byte.
    """
    return len(path) >= _PATH_MAX or len(path.encode("utf-8", "surrogateescape")) >= _PATH_MAX


def _shown(path: str) -> str:
    """*path* as a caveat states it: whole up to :data:`MAX_SHOWN` characters, else cut there and marked."""
    return path if len(path) <= MAX_SHOWN else path[:MAX_SHOWN] + "…"


def _said_key(path: str) -> str:
    """What a reading remembers of a path it stated: the path, or past :data:`MAX_SHOWN` its shown form and digest."""
    if len(path) <= MAX_SHOWN:
        return path
    return f"{_shown(path)}#{hashlib.sha256(path.encode('utf-8', 'surrogateescape')).hexdigest()}"


@dataclass(frozen=True, slots=True)
class _LongPath:
    """A path too long to open, kept as what a caveat states of it and the key it is remembered by."""

    shown: str
    key: str


def _kept(entry: str | None) -> str | _LongPath | None:
    """A playlist entry as DuckStation's and SwanStation's reading keeps it: whole, or a too-long one cut short."""
    if entry is not None and _too_long(entry):
        return _LongPath(_shown(entry), _said_key(entry))
    return entry


def _presence(machine: Machine, path: str) -> bool | None:
    """Whether a file is at *path*: a regular file, nothing, or ``None`` for a directory or a ``stat`` that fails.

    A path the operating system refuses to look up at all — one holding a NUL
    — is one whose presence cannot be told. A path too long to open is never
    looked up: no file is there for the open, which fails on the name.
    """
    if _too_long(path):
        return False
    try:
        kind = machine.path_kind(path)
    except ValueError:
        return None
    if kind == KIND_FILE:
        return True
    return False if kind == KIND_MISSING else None


class _Walk:
    """What one reading has got to: the files listed, the caveats stated, and how the take stands."""

    def __init__(self, machine: Machine, label: str, emulator: str, launched: str) -> None:
        self.machine = machine
        self.label = label
        self.emulator = emulator
        self.launched = launched
        self.listed: dict[str, Opened] = {}
        self.caveats: list[Caveat] = []
        self.sources: list[str] = []
        self.refused = False
        self.unestablished = False
        # Set where the launched file itself could not be read: then nothing is
        # established about what it opens, and the list is no list at all.
        self.launched_unread = False
        self.steps = 0
        self.bytes_read = 0
        self.output = 0
        # What each code and file was stated as: once per effect.
        self._said: dict[tuple[str, str], set[OpensEffect | None]] = {}

    def charge(self, path: str, size: int) -> None:
        """*size* characters more in the answer; past :data:`MAX_OUTPUT` the walk stops short."""
        self.output += size
        if self.output > MAX_OUTPUT:
            raise _Bounded(path)

    def source(self, text: str) -> None:
        self.charge(self.launched, len(text))
        self.sources.append(text)

    def step(self, path: str, count: int = 1) -> None:
        """*count* steps of the walk at *path*; past :data:`MAX_STEPS` the walk stops short."""
        self.steps += count
        if self.steps > MAX_STEPS:
            raise _Bounded(path)

    def text(self, path: str, *, playlist: bool) -> str | None:
        """*path*'s text, read afresh as the emulator reads it again — ``None`` where it did not read as text.

        Each read is a step, and what it consumed is charged to
        :data:`MAX_READ_BYTES` whatever its outcome: the text read, or for a
        read that came back without text the file's size up to its bound and
        the byte past it, which a bounded read takes to tell it is over.
        """
        self.step(path)
        if _too_long(path):
            return None
        limit = psx_region.PLAYLIST_LIMIT if playlist else psx_disc.MAX_CUE_BYTES
        read = psx_region.read_playlist(self.machine, path) if playlist else _read_bounded(self.machine, path, limit)
        text = read.text if read is not None and read.status == READ_OK else None
        if text is not None:
            self.bytes_read += len(text.encode("utf-8", "surrogateescape"))
        else:
            size = self.machine.file_size(path)
            self.bytes_read += 0 if size is None else min(size, limit + 1)
        if self.bytes_read > MAX_READ_BYTES:
            raise _Bounded(path)
        return text

    def list(
        self, path: str, how: OpenedHow, by: str, present: bool | None = None, *, known: bool = False
    ) -> bool | None:
        """List *path* the first time it is opened and say whether it is there; *known* takes *present* as given.

        Every call is a step, a repeated open included.
        """
        self.step(path)
        found = present if known else _presence(self.machine, path)
        if _too_long(path):
            # Never listed: the open fails on the name, which the caller states.
            return False
        if path not in self.listed:
            if len(self.listed) >= MAX_LISTED:
                raise _Bounded(path)
            self.charge(path, len(path) + len(by))
            self.listed[path] = Opened(path, how, by, found)
        return found

    def caveat(
        self,
        code: str,
        path: str,
        message: str,
        kind: str | None = None,
        *,
        free: bool = False,
        key: str | None = None,
        effect: OpensEffect | None = None,
        limit: int | None = None,
    ) -> None:
        """One caveat about *path*, once per code, file and effect; *kind* is the track type a refusal names (``type``).

        *message* spells the file ``{file}``, which becomes the path as shown
        (:func:`_shown`) — so a path longer than that is never copied whole.
        Each is charged to :data:`MAX_OUTPUT`, save the one statement that the
        walk stopped at a bound (*free*). A reading remembers which it stated
        by the path's :func:`_said_key`, or by *key* where only that is kept,
        and by its *effect*, so a stop is stated even where the same file went
        on under the same code before it (D2 of #750). *limit* is the number
        of entries the emulator reads from the file, stated in decimal
        (``limit``, D1 of #754).
        """
        said = (code, key or _said_key(path))
        effects = self._said.setdefault(said, set())
        if effect in effects:
            return
        shown = _shown(path)
        message = message.replace("{file}", shown)
        stated = "" if limit is None else str(limit)
        if not free:
            size = len(message) + len(shown) + len(self.label) + len(kind or "") + len(said[1]) + len(effect or "")
            self.charge(path, size + len(stated))
        effects.add(effect)
        data: dict[str, str] = {"entry": self.label, "file": shown}
        if kind is not None:
            data["type"] = kind
        if effect is not None:
            data["effect"] = effect
        if limit is not None:
            data["limit"] = stated
        self.caveats.append(Caveat(code, message, data))

    def fail(
        self, mode: _Mode, code: str, path: str, what: str, kind: str | None = None, *, key: str | None = None
    ) -> None:
        """A failure of the open: a refusal where the emulator stops there, a statement where it goes on.

        A file that is not there because its path is too long to open is
        stated as that, under its own code. The reason is charged before the
        refusal stands, so a refusal never comes without it (D4).
        """
        if code == CAVEAT_OPENS_FILE_MISSING and _too_long(path):
            code, what = CAVEAT_OPENS_PATH_TOO_LONG, _TOO_LONG
        if mode.stops:
            self.caveat(code, path, f"{self.emulator} {what}, and its open stops there — {self.label!r} does not "
                        "launch this set", kind, key=key, effect=EFFECT_STOPS)
            self.refused = True
        else:
            self.caveat(code, path, f"{self.emulator} {what}, and {mode.goes_on}", kind, key=key, effect=EFFECT_GOES_ON)

    def unsure(self, mode: _Mode, code: str, path: str, what: str, *, effect: OpensEffect | None = None) -> None:
        """Something the take depends on that atlas does not read; it decides the take where the mode says so.

        *effect* is stated where *code* is one a failure of the open is stated
        under (:data:`~atlas.opens_codes.OPENS_FAILURE_CODES`).
        """
        if mode.decisive:
            self.unestablished = True
        if code == CAVEAT_OPENS_FILE_UNREAD and path == self.launched:
            self.launched_unread = True
        self.caveat(code, path, f"{what} — what {self.emulator} makes of it is not established here", effect=effect)

    def reading(self) -> OpensReading:
        outcome = ACCEPTS
        if self.refused:
            outcome = REFUSES
        elif self.unestablished:
            outcome = UNESTABLISHED
        opens = None if self.launched_unread else tuple(self.listed.values())
        return OpensReading(outcome, opens, tuple(self.sources), tuple(self.caveats))


def _read_bounded(machine: Machine, path: str, limit: int) -> ReadResult | None:
    """A text read of at most *limit* bytes — ``None`` where the file holds more."""
    if isinstance(machine, BoundedReadingMachine):
        return machine.read_text_within(path, limit)
    size = machine.file_size(path)
    return None if size is not None and size > limit else machine.read_text(path)


def _replace_extension(path: str, extension: str) -> str:
    """``Path::ReplaceExtension`` (file_system.cpp:600-609 at 1f6de046b): after the last ``.`` of the whole path."""
    dot = path.rfind(".")
    return path if dot < 0 else path[: dot + 1] + extension


def _beside(naming: str, name: str) -> str:
    """``BuildRelativePath`` (file_system.cpp:848-857) for a relative name, ``MDFN_EvalFIP`` alike: the naming file's
    directory with its ``/``, then the name; an absolute name as it is."""
    return name if name.startswith("/") else naming[: naming.rfind("/") + 1] + name


def _unreadable(path: str, label: str, emulator: str) -> OpensReading:
    """The launched file is not a file atlas can read, so what it opens is not established."""
    path = _shown(path)
    return OpensReading(
        UNESTABLISHED,
        None,
        caveats=(
            Caveat(
                CAVEAT_OPENS_FILE_UNREAD,
                f"{path} is not a file atlas could read, so what {emulator} opens through it is not established — "
                "never a refusal, so a client can ask before it places the file",
                {"entry": label, "file": path},
            ),
        ),
    )


def _reader(
    machine: Machine, profile: Profile, label: str, content_path: str, patches: PatchReader | None
) -> _DuckReader | _MednafenReader | _FlycastReader | _GpgxReader | None:
    """The reader of *profile*'s open of *content_path*, or ``None`` where this reading covers no file of its kind."""
    if isinstance(profile, DuckProfile):
        if _duck_kind(content_path) not in _DUCK_LAUNCHED:
            return None
        return _DuckReader(machine, profile, label, content_path, patches)
    if isinstance(profile, MednafenProfile):
        reads = _mednafen_reads(profile, content_path)
        return _MednafenReader(machine, profile, label, content_path) if reads else None
    if isinstance(profile, FlycastProfile):
        return _FlycastReader(machine, profile, label, content_path) if _flycast_reads(content_path) else None
    return _GpgxReader(machine, profile, label, content_path) if _gpgx_reads(content_path) else None


def read_opens(
    machine: Machine,
    profile: Profile,
    label: str,
    content_path: str,
    patches: PatchReader | None = None,
) -> OpensReading | None:
    """What *profile*'s emulator opens through *content_path* when the entry *label* launches it.

    ``None`` where this cut reads no open of the file's kind for that
    emulator; the entry is then judged as it was before. *patches* reads the
    setting that loads ``.ppf`` patches, asked only where a reading needs it.
    """
    reader = _reader(machine, profile, label, content_path, patches)
    if reader is None:
        return None
    if _presence(machine, content_path) is not True:
        return _unreadable(content_path, label, profile.name)
    walk = reader.walk
    try:
        reader.read(content_path)
    except _Bounded as bounded:
        walk.unestablished = True
        walk.caveat(
            CAVEAT_OPENS_BOUND_EXCEEDED,
            bounded.path,
            f"the walk reached one of atlas's bounds at this file ({MAX_LISTED} files, {MAX_NESTING} nested "
            f"playlists, {MAX_TRACKS} tracks in a sheet, {MAX_STEPS} steps, {MAX_READ_BYTES} bytes read, "
            f"{MAX_OUTPUT} characters of answer), so what "
            "is opened past it is not read",
            free=True,
        )
    return walk.reading()


# ── DuckStation and SwanStation ────────────────────────────────────────────

_DUCK_KINDS = {
    ".cue": "cue",
    ".bin": "bin",
    ".img": "bin",
    ".iso": "bin",
    ".mds": "mds",
    ".m3u": "m3u",
    ".chd": "unread",
    ".ecm": "unread",
    ".pbp": "unread",
}
_DUCK_LAUNCHED = frozenset({"cue", "bin", "mds", "m3u"})


def _duck_kind(path: str) -> str:
    """``CDImage::Open``'s dispatch: the suffix from the last ``.`` of the whole path, ignoring case (``strrchr``)."""
    dot = path.rfind(".")
    return "refused" if dot < 0 else _DUCK_KINDS.get(path[dot:].lower(), "refused")


_AT_LAUNCH = _Mode(stops=True, decisive=True)
_ON_SWITCH = _Mode(
    stops=False,
    decisive=False,
    goes_on="this disc is opened only when the player switches to it, so the launch goes on",
)


class _DuckReader:
    """DuckStation's and SwanStation's open of one launched file."""

    def __init__(
        self, machine: Machine, profile: DuckProfile, label: str, launched: str, patches: PatchReader | None
    ) -> None:
        self.machine = machine
        self.profile = profile
        self.walk = _Walk(machine, label, profile.name, launched)
        self._patches = patches
        self._setting: PatchSetting | None = None
        # What a reading keeps of a file it may visit again: a playlist's first
        # entry (None where it names none, cut short where too long to open), a
        # sheet's tracks within MAX_TRACKS or the parser's refusal — never the
        # file's text, and all of it charged to the answer bound.
        self._firsts: dict[str, str | _LongPath | None] = {}
        self._sheets: dict[str, list[psx_disc.CueTrack] | psx_disc.NotADisc | None] = {}

    def read(self, path: str) -> None:
        self.walk.source(
            f"entry {self.walk.label!r}: {self.profile.name} opens {path} through CDImage::Open (cd_image.cpp)"
        )
        later: Iterator[str] = iter(())
        if _duck_kind(path) == "m3u":
            text = self.walk.text(path, playlist=True)
            if text is None:
                self.walk.unsure(_AT_LAUNCH, CAVEAT_OPENS_FILE_UNREAD, path, _UNREAD)
                return
            later = self._entries(text, path)
            self._keep_first(path, next(later, None))
        if not self._image(path, _AT_LAUNCH, ()):
            return
        if not self.profile.patches_every_image:
            self._ppf(path, _AT_LAUNCH)
        for entry in later:
            self._entry(entry, path, _ON_SWITCH, (path,))

    def _entries(self, text: str, path: str) -> Iterator[str]:
        """``CDImageM3u::Open``'s entries as they are reached, every line read a step."""
        return psx_region.iter_duckstation_playlist_entries(text, path, lambda: self.walk.step(path))

    def _keep_first(self, path: str, entry: str | None) -> None:
        kept = _kept(entry)
        size = 0
        if isinstance(kept, str):
            size = len(kept)
        elif kept is not None:
            size = len(kept.shown) + len(kept.key)
        self.walk.charge(path, len(path) + size)
        self._firsts[path] = kept

    def _first(self, path: str, mode: _Mode) -> tuple[bool, str | _LongPath | None]:
        """Whether the playlist read, and its first entry — read once per spelling, kept alone."""
        if path not in self._firsts:
            text = self.walk.text(path, playlist=True)
            if text is None:
                self.walk.unsure(mode, CAVEAT_OPENS_FILE_UNREAD, path, _UNREAD)
                return False, None
            self._keep_first(path, next(self._entries(text, path), None))
        return True, self._firsts[path]

    def _image(self, path: str, mode: _Mode, chain: tuple[str, ...]) -> bool:
        """``CDImage::Open`` of one path: its own open, then its ``.ppf`` where every image is patched."""
        kind = _duck_kind(path)
        if kind == "m3u":
            opened = self._nested(path, mode, chain)
        elif kind == "cue":
            opened = self._cue(path, mode)
        elif kind == "mds":
            opened = self._mds(path, mode)
        elif kind == "bin":
            opened = self._companions(path)
        elif kind == "unread":
            self.walk.unsure(mode, CAVEAT_OPENS_FORMAT_UNREAD, path, _NOT_FOLLOWED)
            return False
        else:
            self.walk.fail(mode, CAVEAT_OPENS_FORMAT_REFUSED, path, "opens no image of this name's suffix")
            return False
        if opened and self.profile.patches_every_image:
            self._ppf(path, mode)
        return opened

    def _nested(self, path: str, mode: _Mode, chain: tuple[str, ...]) -> bool:
        """A playlist opens its first entry; the launched one's other entries wait for a disc switch."""
        read, first = self._first(path, mode)
        if not read:
            return False
        if first is None:
            self.walk.fail(mode, CAVEAT_OPENS_SHEET_REFUSED, path, "reads no entry from this playlist")
            return False
        if isinstance(first, _LongPath):
            self.walk.step(first.shown)
            self.walk.fail(mode, CAVEAT_OPENS_PATH_TOO_LONG, first.shown, _TOO_LONG, key=first.key)
            return False
        return self._entry(first, path, mode, (*chain, path))

    def _entry(self, entry: str, playlist: str, mode: _Mode, chain: tuple[str, ...]) -> bool:
        """One playlist entry, opened through the same dispatch as any content."""
        present = self.walk.list(entry, HOW_NAMED, playlist)
        if present is None:
            self.walk.unsure(mode, CAVEAT_OPENS_FILE_UNREAD, entry, _UNTOLD)
            return False
        if not present:
            self.walk.fail(mode, CAVEAT_OPENS_FILE_MISSING, entry, _MISSING)
            return False
        if entry in chain:
            self.walk.fail(mode, CAVEAT_OPENS_SHEET_REFUSED, entry, "reaches this playlist again through its own "
                           "entries, a recursion it never returns from")
            return False
        if len(chain) >= MAX_NESTING:
            raise _Bounded(entry)
        return self._image(entry, mode, chain)

    def _cue(self, path: str, mode: _Mode) -> bool:
        """``CDImageCueSheet::OpenAndParse``: the sheet parsed whole, then its files opened track by track."""
        if path not in self._sheets:
            text = self.walk.text(path, playlist=False)
            if text is None:
                self.walk.unsure(mode, CAVEAT_OPENS_FILE_UNREAD, path, _NOT_A_SHEET)
                return False
            # The parser takes the sheet whole: every line of it a step, and every
            # 1023-byte piece fgets cuts a longer line into, counted from above.
            pieces = len(text.encode("utf-8", "surrogateescape")) // psx_disc.CUE_LINE
            self.walk.step(path, text.count("\n") + 1 + pieces)
            parsed = self._sheets[path] = _parsed_sheet(text)
            self.walk.charge(path, len(path) + _sheet_size(parsed))
        parsed = self._sheets[path]
        if parsed is None:
            raise _Bounded(path)
        if isinstance(parsed, psx_disc.NotADisc):
            return self._refused_sheet(path, parsed, mode)
        tracks = _by_number(parsed)
        if _starts_backwards(parsed, tracks):
            self.walk.fail(mode, CAVEAT_OPENS_SHEET_REFUSED, path, "refuses this cue sheet: a track starts before "
                           "the one ahead of it in the same file (SetTrackLengths)")
            return False
        return self._track_files(path, tracks, mode) and self._companions(path)

    def _refused_sheet(self, path: str, refused: psx_disc.NotADisc, mode: _Mode) -> bool:
        """The parser's refusal of the sheet, before any file opens; a refused ``FILE`` type names its file."""
        if isinstance(refused, psx_disc.NotBinaryFile):
            # The file that failed is listed all the same, so the list names it (D4).
            kind = os.fsdecode(refused.kind)
            failed = _beside(path, os.fsdecode(refused.name))
            self.walk.list(failed, HOW_NAMED, path)
            self.walk.fail(
                mode,
                CAVEAT_OPENS_TRACK_REFUSED,
                failed,
                f"takes no FILE of type {kind!r} — its cue parser takes BINARY alone (cue_parser.cpp:227-231)",
                kind,
            )
        else:
            self.walk.fail(mode, CAVEAT_OPENS_SHEET_REFUSED, path, f"refuses this cue sheet: {refused}")
        return False

    def _track_files(self, cue: str, tracks: dict[int, psx_disc.CueTrack], mode: _Mode) -> bool:
        """Track 1 upward until a number is missing, each file once by its spelled name (cd_image_cue.cpp:83-128)."""
        opened: set[bytes] = set()
        for number in range(1, 100):
            track = tracks.get(number)
            if track is None:
                break
            if track.file in opened:
                continue
            if not self._track_file(cue, os.fsdecode(track.file), first=not opened, mode=mode):
                return False
            opened.add(track.file)
        return True

    def _track_file(self, cue: str, name: str, *, first: bool, mode: _Mode) -> bool:
        path = _beside(cue, name)
        present = self.walk.list(path, HOW_NAMED, cue)
        if present:
            return True
        if present is None:
            self.walk.unsure(mode, CAVEAT_OPENS_FILE_UNREAD, path, _UNTOLD)
            return False
        if first:
            recovery = _replace_extension(cue, "bin")
            found = self.walk.list(recovery, HOW_NAME_DERIVED, cue)
            if found:
                self.walk.source(
                    f"{cue} names {path}, which is not there, and {self.profile.name} opens {recovery} in its place, "
                    "the cue's own name with .bin, for the first file alone"
                )
                return True
            if found is None:
                self.walk.unsure(mode, CAVEAT_OPENS_FILE_UNREAD, recovery, _UNTOLD)
                return False
        self.walk.fail(mode, CAVEAT_OPENS_FILE_MISSING, path, _MISSING)
        return False

    def _mds(self, path: str, mode: _Mode) -> bool:
        """An ``.mds`` reads its own descriptor and opens the ``.mdf`` of its name."""
        mdf = _replace_extension(path, "mdf")
        present = self.walk.list(mdf, HOW_NAME_DERIVED, path)
        if present is None:
            self.walk.unsure(mode, CAVEAT_OPENS_FILE_UNREAD, mdf, _UNTOLD)
            return False
        if not present:
            self.walk.fail(mode, CAVEAT_OPENS_FILE_MISSING, mdf, _MISSING)
            return False
        self.walk.unsure(
            mode, CAVEAT_OPENS_CONTENT_UNREAD, path, "{file} is a descriptor whose bytes atlas does not read"
        )
        return self._companions(path)

    def _companions(self, image: str) -> bool:
        """The ``.sbi`` of the image's name, and DuckStation's ``.lsd`` where that is not present."""
        sbi = self._companion(_replace_extension(image, "sbi"), image)
        if self.profile.looks_for_lsd and sbi is not True:
            self._companion(_replace_extension(image, "lsd"), image)
        return True

    def _companion(self, path: str, image: str) -> bool | None:
        present = self.walk.list(path, HOW_NAME_DERIVED, image)
        if present is None:
            self.walk.caveat(CAVEAT_OPENS_FILE_UNREAD, path, _UNTOLD_THERE)
        return present

    def _ppf(self, image: str, mode: _Mode) -> None:
        """The ``.ppf`` of the image's file name beside it, where the setting loads patches or could not be read.

        A setting nobody read may load them, so the patch is looked for either
        way (#776 D9). Where a patch the emulator cannot apply fails the open,
        one that is there, or one whose presence cannot be told, leaves the
        open not established; where it reopens the image unpatched, nothing
        about the patch decides the open.
        """
        setting = self._patch_setting()
        if setting.loads is False:
            return
        slash = image.rfind("/") + 1
        ppf = image[:slash] + _replace_extension(image[slash:], "ppf")
        present = self.walk.list(ppf, HOW_NAME_DERIVED, image)
        if not self.profile.patch_failure_fails_open:
            return
        if present is None:
            self.walk.unsure(
                mode,
                CAVEAT_OPENS_FILE_UNREAD,
                ppf,
                f"{_UNTOLD_THERE}, and a patch it cannot apply fails the open",
            )
        elif present:
            self.walk.unsure(
                mode,
                CAVEAT_OPENS_CONTENT_UNREAD,
                ppf,
                "{file} is a patch whose bytes atlas does not read, and one it cannot apply fails the open",
            )

    def _patch_setting(self) -> PatchSetting:
        if self._setting is None:
            self._setting = self._patches() if self._patches is not None else PatchSetting(None, "")
            for source in self._setting.sources:
                self.walk.source(source)
            caveats = list(self._setting.caveats)
            if self._setting.loads is None:
                caveats.append(
                    Caveat(
                        CAVEAT_OPENS_SETTING_UNREAD,
                        f"whether {self.profile.name} loads .ppf patches ({self._setting.setting}) could not be read, "
                        "so whether it opens the patch of an image's name is not established",
                        {"entry": self.walk.label, "setting": self._setting.setting},
                    )
                )
            for caveat in caveats:
                self.walk.charge(self.walk.launched, len(caveat.message) + len(str(caveat.data)))
                self.walk.caveats.append(caveat)
        return self._setting


def _parsed_sheet(text: str) -> list[psx_disc.CueTrack] | psx_disc.NotADisc | None:
    """The sheet's tracks, the parser's refusal, or ``None`` past :data:`MAX_TRACKS` — what a reading keeps of it."""
    try:
        tracks = psx_disc.parse_cue(text.encode("utf-8", "surrogateescape"))
    except psx_disc.NotADisc as refused:
        return refused
    return tracks if len(tracks) <= MAX_TRACKS else None


def _sheet_size(parsed: list[psx_disc.CueTrack] | psx_disc.NotADisc | None) -> int:
    """The characters a kept sheet holds: its tracks' files, or the refusal with the file and type it names."""
    if isinstance(parsed, list):
        return sum(len(track.file) for track in parsed)
    if isinstance(parsed, psx_disc.NotBinaryFile):
        return len(str(parsed)) + len(parsed.name) + len(parsed.kind)
    return len(str(parsed or ""))


def _by_number(tracks: list[psx_disc.CueTrack]) -> dict[int, psx_disc.CueTrack]:
    """``GetTrack``'s answer per number: the first track the sheet declares under it (cue_parser.cpp:31-39)."""
    first: dict[int, psx_disc.CueTrack] = {}
    for track in tracks:
        first.setdefault(track.number, track)
    return first


def _starts_backwards(tracks: list[psx_disc.CueTrack], by_number: dict[int, psx_disc.CueTrack]) -> bool:
    """``SetTrackLengths``: a track whose predecessor in the same file starts after it (cue_parser.cpp:442-470)."""
    for track in tracks:
        previous = by_number.get(track.number - 1)
        if previous is not None and previous.file == track.file and previous.indices[1] > track.indices[1]:
            return True
    return False


# ── The mednafen cores ─────────────────────────────────────────────────────


def _is_playlist(path: str) -> bool:
    """The loaders' test of a launched ``.m3u``: the last four characters, ignoring case (libretro.cpp:4105)."""
    return len(path) > 4 and path[-4:].lower() == psx_region.PLAYLIST_SUFFIX


def _suffix(path: str) -> str:
    """``MDFN_GetFilePathComponents``'s extension: from the last ``.`` of the file name, with the dot."""
    name = path[path.rfind("/") + 1 :]
    dot = name.rfind(".")
    return "" if dot < 0 else name[dot:]


def _mednafen_reads(profile: MednafenProfile, path: str) -> bool:
    """Whether this cut reads the core's open of a launched *path*: a cue sheet, a ``.ccd`` or a playlist."""
    if _is_playlist(path):
        return profile.playlist != PLAYLIST_UNREAD
    return path[-3:].lower() == "ccd" or _suffix(path).lower() == ".cue"


_NO_DISC = "the core loads this playlist's discs whether each opened or not, so the load goes on without it"
_NEWER_GOES_ON = "the core discards what the open returns and goes on without it"


class _MednafenReader:
    """One mednafen core's open of one launched file."""

    def __init__(self, machine: Machine, profile: MednafenProfile, label: str, launched: str) -> None:
        self.machine = machine
        self.profile = profile
        self.walk = _Walk(machine, label, profile.name, launched)
        self.direct = _Mode(stops=profile.stops, decisive=True, goes_on=_NEWER_GOES_ON)
        self.disc = _Mode(stops=False, decisive=True, goes_on=_NEWER_GOES_ON if profile.newer else _NO_DISC)

    def read(self, path: str) -> None:
        self.walk.source(f"entry {self.walk.label!r}: {self.profile.name} opens {path} through its loader")
        toc = psx_region.beetle_toc(self.machine, path) if self.profile.loads_toc_beside else None
        if toc is not None:
            self.walk.list(toc, HOW_NAME_DERIVED, path)
            self.walk.unsure(self.direct, CAVEAT_OPENS_FORMAT_UNREAD, toc,
                             "{file} is loaded in place of the launched file, and this reading does not follow a .toc")
        elif _is_playlist(path):
            self._playlist(path)
        else:
            self._image(path, self.direct)

    def _playlist(self, path: str) -> None:
        """``ReadM3U`` and every disc it names opened; Beetle PCE and PCE Fast then fail the load whatever it named."""
        refused = self.profile.playlist == PLAYLIST_REFUSED
        discs: list[tuple[str, str]] = []
        text = self.walk.text(path, playlist=True)
        walked = False
        if text is not None:
            walked = self._walk_playlist(path, text, (), discs)
        elif not refused:
            self.walk.unsure(self.direct, CAVEAT_OPENS_FILE_UNREAD, path, _UNREAD)
            return
        for disc, playlist in discs if walked else ():
            self._disc(disc, playlist)
        if refused:
            self.walk.fail(
                _AT_LAUNCH,
                CAVEAT_OPENS_PLAYLIST_REFUSED,
                path,
                "reads a playlist and opens its discs, and then fails the load, because its loader marks a load "
                f"good only in its single-disc branch ({self.profile.loader})",
            )
        elif walked and not discs:
            self.walk.unsure(self.direct, CAVEAT_OPENS_CONTENT_UNREAD, path,
                             "{file} names no disc, and what the core does with none is not read")

    def _walk_playlist(self, playlist: str, text: str, stack: tuple[str, ...], discs: list[tuple[str, str]]) -> bool:
        """``ReadM3U``: nested playlists, named in lowercase ``.m3u``, followed in place; ``False`` on a loop.

        Read afresh on every visit, as the core reads it, every line a step.
        """
        for entry in psx_region.iter_beetle_playlist_lines(text, playlist, lambda: self.walk.step(playlist)):
            if not entry.endswith(psx_region.PLAYLIST_SUFFIX):
                # Held until the whole list is read, so charged as it is held.
                self.walk.charge(entry, len(entry))
                discs.append((entry, playlist))
                continue
            if entry == playlist:
                break
            self.walk.list(entry, HOW_NAMED, playlist)
            if entry in stack:
                self.walk.fail(_AT_LAUNCH, CAVEAT_OPENS_SHEET_REFUSED, entry, "reaches this playlist again through "
                               "its own entries, a recursion it never returns from")
                return False
            if len(stack) >= MAX_NESTING:
                raise _Bounded(entry)
            if not self._nested(entry, (*stack, playlist), discs):
                return False
        return True

    def _nested(self, playlist: str, stack: tuple[str, ...], discs: list[tuple[str, str]]) -> bool:
        text = self.walk.text(playlist, playlist=True)
        if text is not None:
            return self._walk_playlist(playlist, text, stack, discs)
        if _presence(self.machine, playlist) is False:
            self.walk.fail(self.disc, CAVEAT_OPENS_FILE_MISSING, playlist, _MISSING)
        else:
            self.walk.unsure(self.disc, CAVEAT_OPENS_FILE_UNREAD, playlist, _UNREAD)
        return True

    def _disc(self, disc: str, playlist: str) -> None:
        present = self.walk.list(disc, HOW_NAMED, playlist)
        if present is None:
            self.walk.unsure(self.disc, CAVEAT_OPENS_FILE_UNREAD, disc, _UNTOLD)
        elif not present:
            self.walk.fail(self.disc, CAVEAT_OPENS_FILE_MISSING, disc, _MISSING)
        else:
            self._image(disc, self.disc)

    def _image(self, path: str, mode: _Mode) -> None:
        """``CDAccess_Open``'s dispatch by the last three characters, then the image's own open (CDAccess.cpp)."""
        if path[-3:].lower() == "ccd":
            self._ccd(path, mode)
        elif _suffix(path).lower() == ".cue":
            _MednafenSheet(self, path, mode).open()
        else:
            self.walk.unsure(mode, CAVEAT_OPENS_FORMAT_UNREAD, path, _NOT_FOLLOWED)

    def _ccd(self, path: str, mode: _Mode) -> None:
        """``CDAccess_CCD::Load``: the sections first, then the ``.img`` and the ``.sub`` of its name."""
        self.walk.unsure(mode, CAVEAT_OPENS_CONTENT_UNREAD, path, "{file} is a descriptor whose sections atlas does "
                         "not read")
        directory, base = path[: path.rfind("/") + 1], path[path.rfind("/") + 1 :]
        stem = base[: base.rfind(".")] if "." in base else base
        for extension in ccd_extensions(_suffix(path)):
            companion = f"{directory}{stem}.{extension}"
            present = self.walk.list(companion, HOW_NAME_DERIVED, path)
            if present is None:
                self.walk.unsure(mode, CAVEAT_OPENS_FILE_UNREAD, companion, _UNTOLD)
                return
            if not present:
                self.walk.fail(mode, CAVEAT_OPENS_FILE_MISSING, companion, _MISSING)
                return


def _cased(extension: str, letters: str) -> str:
    """*letters* uppercased where the matching letter of a four-character *extension* is (``sbi_ext``)."""
    if len(extension) != 4 or extension[0] != ".":
        return letters
    return "".join(c.upper() if "A" <= e <= "Z" else c for c, e in zip(letters, extension[1:]))


def ccd_extensions(extension: str) -> tuple[str, str]:
    """``img`` and ``sub``, cased the way ``CDAccess_CCD::Load`` cases them (CDAccess_CCD.cpp:86-126).

    Each letter of a four-character extension says upper or lower where it is
    an ASCII letter; a character that is not takes the case of the letter
    before it, or, where no letter comes before, the case of the last letter,
    and lower where there is no letter at all.
    """
    if len(extension) != 4 or extension[0] != ".":
        return "img", "sub"
    cases: list[bool | None] = [c.isupper() if c.isascii() and c.isalpha() else None for c in extension[1:]]
    last: bool | None = None
    for index, case in enumerate(cases):
        if case is None:
            cases[index] = last
        else:
            last = case
    upper = [bool(last) if case is None else case for case in cases]
    img = "".join(c.upper() if u else c for c, u in zip("img", upper))
    sub = "".join(c.upper() if u else c for c, u in zip("sub", upper))
    return img, sub


# The bytes ``MDFN_trim`` strips (general.cpp) and the ones ``get_line`` ends a line at (MemoryStream.cpp:170-185).
_MEDNAFEN_SPACE = " \r\n\t\x0b"
_LINE_END = re.compile("[\r\n\x00]")
_INFO_COMMANDS = frozenset({"CDTEXTFILE", "CATALOG", "ISRC", "TITLE", "PERFORMER", "SONGWRITER"})
_FLAG_WORDS = frozenset({"DCP", "4CH", "PRE", "SCMS"})
_C_SPACES = "[ \t\n\x0b\x0c\r]*"
_UNSIGNED = re.compile(_C_SPACES + r"([+-]?)(\d+)")
_MSF = re.compile(f"{_C_SPACES}([+-]?\\d+):{_C_SPACES}([+-]?\\d+):{_C_SPACES}([+-]?\\d+)")
_ATOI = re.compile(_C_SPACES + r"([+-]?\d+)")
_U32 = 1 << 32


def _mednafen_lines(text: str, on_line: Callable[[], None]) -> Iterator[str]:
    """The lines ``ImageOpen`` reads after a UTF-8 byte order mark: ended by CR, LF or NUL, trimmed, empty ones skipped.

    ``get_line`` answers end of stream for a last line no terminator ends, so
    that line is never read. The lines come one at a time, and *on_line* is
    called for every one read, empty or not.
    """
    start = 1 if text.startswith("\ufeff") else 0
    for end in _LINE_END.finditer(text, start):
        on_line()
        line = text[start : end.start()].strip(_MEDNAFEN_SPACE)
        start = end.end()
        if line:
            yield line


def _unquotify(line: str, offset: int, parse_quotes: bool = True) -> tuple[str, int]:
    """``UnQuotify`` (CDAccess_Image.cpp:137-195): one argument from *offset*, and where the next one starts."""
    out: list[str] = []
    in_quote = already = False
    while offset < len(line):
        char = line[offset]
        if char in " \t" and not in_quote:
            if already:
                break
            offset += 1
            continue
        if char == '"' and parse_quotes:
            offset += 1
            if in_quote:
                break
            in_quote = True
            continue
        out.append(char)
        already = True
        offset += 1
    while offset < len(line) and line[offset] in " \t":
        offset += 1
    return "".join(out), offset


def _ascii_upper(text: str) -> str:
    """``MDFN_strtoupper`` and ``strcasecmp``'s folding: ``a-z`` only."""
    return "".join(chr(ord(c) - 32) if "a" <= c <= "z" else c for c in text)


def _command(line: str) -> tuple[str, list[str], int]:
    """A line's command, upper-cased, and up to four arguments with how many the line held."""
    command, offset = _unquotify(line, 0, parse_quotes=False)
    args: list[str] = []
    while len(args) < 4 and offset < len(line):
        arg, offset = _unquotify(line, offset)
        args.append(arg)
    count = len(args)
    return _ascii_upper(command), args + [""] * (4 - count), count


def _unsigned(value: str) -> int:
    """A ``%u`` conversion's value: the sign applied and wrapped to 32 bits."""
    return int(value) % _U32


def _scan_msf(text: str) -> tuple[int, int, int] | None:
    """``sscanf(text, "%u:%u:%u")`` succeeding with three values, or ``None``."""
    match = _MSF.match(text)
    return None if match is None else (_unsigned(match[1]), _unsigned(match[2]), _unsigned(match[3]))


def _msf_in_range(text: str) -> bool:
    """``StringToMSF`` (Saturn CDAccess_Image.cpp:350-359): three values and no field past 99:59:74."""
    msf = _scan_msf(text)
    return msf is not None and msf[0] <= 99 and msf[1] <= 59 and msf[2] <= 74


def _atoi(text: str) -> int:
    match = _ATOI.match(text)
    return 0 if match is None else int(match[1])


class _MednafenSheet:
    """``CDAccess_Image::ImageOpen`` over one cue sheet, its files opened as its lines reach them."""

    def __init__(self, reader: _MednafenReader, path: str, mode: _Mode) -> None:
        self.reader = reader
        self.walk = reader.walk
        self.profile = reader.profile
        self.path = path
        self.mode = mode
        self.base = path[: path.rfind("/")] if "/" in path else "."
        self.active = -1
        self.first = 99
        self.last = 0

    def open(self) -> None:
        text = self.walk.text(self.path, playlist=False)
        if text is None:
            self.walk.unsure(self.mode, CAVEAT_OPENS_FILE_UNREAD, self.path, _NOT_A_SHEET)
            return
        for line in _mednafen_lines(text, lambda: self.walk.step(self.path)):
            command, args, count = _command(line)
            if command == "FILE":
                if not self._file(args):
                    return
            elif not self._grammar(command, args, count):
                self.walk.fail(self.mode, CAVEAT_OPENS_SHEET_REFUSED, self.path,
                               f"refuses this cue sheet at its {command!r} line")
                return
        if self.first > self.last:
            self.walk.fail(self.mode, CAVEAT_OPENS_SHEET_REFUSED, self.path, "finds no track in this cue sheet")
            return
        self._sbi()

    def _file(self, args: list[str]) -> bool:
        """A ``FILE`` line: the file opened, then its type read; ``False`` where the sheet's read ends there."""
        self.active = -1
        name = args[0]
        # A name holding cdrom:// is opened as written, against a working
        # directory nothing here records (CDAccess_Image.cpp:634-637).
        verbatim = "cdrom://" in name
        path = name if verbatim else _beside(self.base + "/", name)
        present = self.walk.list(path, HOW_NAMED, self.path, None, known=verbatim)
        if present is None:
            self.walk.unsure(self.mode, CAVEAT_OPENS_FILE_UNREAD, path, _UNTOLD)
            return False
        if not present:
            self.walk.fail(self.mode, CAVEAT_OPENS_FILE_MISSING, path, _MISSING)
            if not self.profile.newer:
                return False
        kind = _ascii_upper(args[1])
        if kind in self.profile.raw_types:
            return True
        if kind in self.profile.reader_types:
            if present:
                self.walk.unsure(self.mode, CAVEAT_OPENS_CONTENT_UNREAD, path, f"{{file}} is a {kind} track an audio "
                                 "reader opens by its bytes, which atlas does not read")
            return present
        self.walk.fail(self.mode, CAVEAT_OPENS_TRACK_REFUSED, path, f"takes no FILE of type {args[1]!r}", args[1])
        return False

    def _grammar(self, command: str, args: list[str], count: int) -> bool:
        """Every other line: whether the sheet's read goes on past it."""
        if command == "TRACK":
            return self._track(args)
        if command in ("INDEX", "PREGAP", "POSTGAP"):
            return self.active < 0 or self._time(command, args)
        if command == "FLAGS":
            return all(arg in _FLAG_WORDS for arg in args[:count])
        return command == "REM" or command in _INFO_COMMANDS

    def _track(self, args: list[str]) -> bool:
        number = _atoi(args[0])
        formats = _NEWER_FORMATS if self.profile.newer else _PSX_FORMATS
        lowest = 1 if self.profile.newer else 0
        if not lowest <= number <= 99 or _ascii_upper(args[1]) not in formats:
            return False
        self.active = number
        self.first, self.last = min(self.first, number), max(self.last, number)
        return True

    def _time(self, command: str, args: list[str]) -> bool:
        stated = args[1] if command == "INDEX" else args[0]
        if not self.profile.newer:
            return _scan_msf(stated) is not None
        if command != "INDEX":
            return _msf_in_range(stated)
        index = _UNSIGNED.match(args[0])
        return _msf_in_range(stated) and index is not None and _unsigned(index[1] + index[2]) < 100

    def _sbi(self) -> None:
        """The ``.sbi`` of the cue's name, cased like its extension; the newer cores fail on one they cannot read."""
        name = self.path[self.path.rfind("/") + 1 :]
        stem = name[: name.rfind(".")] if "." in name else name
        sbi = f"{self.base}/{stem}.{_cased(_suffix(self.path), 'sbi')}"
        present = self.walk.list(sbi, HOW_NAME_DERIVED, self.path)
        if present is None:
            self.walk.caveat(CAVEAT_OPENS_FILE_UNREAD, sbi, _UNTOLD_THERE)
        elif present and self.profile.checks_sbi:
            self.walk.unsure(self.mode, CAVEAT_OPENS_CONTENT_UNREAD, sbi, "{file} is read by its bytes, which atlas "
                             "does not read, and one the core refuses ends the open")


# ── Byte-level helpers the two newer readers share ─────────────────────────


def _bytes(path: str) -> bytes:
    """*path* as the bytes a C string holds."""
    return path.encode("utf-8", "surrogateescape")


def _path(raw: bytes) -> str:
    """A C string's bytes as a path."""
    return raw.decode("utf-8", "surrogateescape")


def _ascii_lower(text: str) -> str:
    """``tolower`` in the C locale: ``A-Z`` only."""
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


def _gets(data: bytes, size: int) -> Iterator[bytes]:
    """``fgets`` into a *size*-byte buffer: a piece ends after ``\\n`` or after *size* - 1 bytes, whichever is first."""
    start = 0
    while start < len(data):
        newline = data.find(b"\n", start, start + size - 1)
        end = start + size - 1 if newline < 0 else newline + 1
        yield data[start:end]
        start = end


def _c_string(piece: bytes) -> bytes:
    """What a C string holds of *piece*: everything before its first NUL."""
    return piece.split(b"\x00", 1)[0]


# ── Flycast ────────────────────────────────────────────────────────────────

# The sizes from which Flycast refuses a sheet as too big (gdi.cpp:18-24,
# cue.cpp:62-68 at 1dac369), measured by the file's size before any read.
_GDI_LIMIT: Final = 8193
_FLYCAST_CUE_LIMIT: Final = 64 << 10
# What retro_load_game keeps of the content's file name and directory
# (g_base_name[128], game_dir[1024], libretro.cpp:204-205), and read_m3u's
# buffer for a line and for a disc's path (line[PATH_MAX], name[PATH_MAX]).
_FLYCAST_NAME: Final = 127
_FLYCAST_DIR: Final = 1023
_FLYCAST_LINE: Final = 4096
# The name extensions loadGame takes as an arcade cartridge (getGamePlatform,
# emulator.cpp:526-545; naomi_cart_GetPlatform never answers Dreamcast).
_FLYCAST_ARCADE = frozenset({"zip", "7z", "bin", "dat", "lst"})
# The track modes cue_parse gives a sector size; any other fails the open
# after its file opened (getSectorSize, cue.cpp:25-44, :188-194).
_FLYCAST_MODES = frozenset(
    {b"AUDIO", b"CDG", b"MODE1/2048", b"MODE1/2352", b"MODE2/2336", b"MODE2/2352", b"CDI/2336", b"CDI/2352"}
)
_FLYCAST_LEAST_TRACKS: Final = 3
_DIGITS: Final = b"0123456789"
# Single-class runs, matched at a position, so a long field is scanned at C speed.
_SPACE_RUN = re.compile(rb"[ \t\n\v\f\r]*")
_TOKEN_RUN = re.compile(rb"[^ \t\n\v\f\r]*")
_DIGIT_RUN = re.compile(rb"\d*")  # a bytes pattern: \d is the ten ASCII digits alone
# What a parser that reads a variable it never set is said to do, and one that
# reads a quoted name no quote ends.
_UNDEFINED = "{file} leaves the parser reading a value it never set, so what it reads next is undefined"
_UNENDING = "reads a quoted name in {file} that no quote ends, and its parser loops on it without end"


def _flycast_extension(path: str) -> str:
    """``get_file_extension`` (stdclass.h:125-133): after the last ``.`` of the whole path, lowercased."""
    dot = path.rfind(".")
    return "" if dot < 0 else _ascii_lower(path[dot + 1 :])


def _flycast_driver(path: str) -> str | None:
    """The driver of ``OpenDisc`` that takes *path*, in its order (common.cpp:18-27, :86-97).

    The libretro build's CHD test is the path's last three characters exactly
    (chd.cpp:252-254); the others compare the lowercased extension.
    """
    if path.endswith("chd"):
        return "chd"
    extension = _flycast_extension(path)
    return extension if extension in ("gdi", "cdi", "cue") else None


def _flycast_playlist(path: str) -> bool:
    """retro_load_game's playlist test: ``.m3u`` or ``.M3U`` from the last ``.`` of the name cut to 127 bytes.

    ``extract_basename`` keeps the first 127 bytes of the file name
    (libretro.cpp:1889-1899, :2117) and ``strrchr`` finds the extension in
    what it kept (:2159, :2187).
    """
    name = _bytes(path[path.rfind("/") + 1 :])[:_FLYCAST_NAME]
    dot = name.rfind(b".")
    return dot >= 0 and name[dot:] in (b".m3u", b".M3U")


def _flycast_reads(path: str) -> bool:
    """Whether this cut reads Flycast's open of a launched *path*: a ``.gdi``, a ``.cue`` or a playlist."""
    return _flycast_playlist(path) or _flycast_driver(path) in ("gdi", "cue") or _flycast_extension(path) == "m3u"


def _flycast_roms_dir(playlist: str) -> bytes:
    """``g_roms_dir``: the directory of the path cut to 1023 bytes, and a ``/`` (libretro.cpp:1353-1363, :2118-2121)."""
    kept = _bytes(playlist)[:_FLYCAST_DIR]
    slash = kept.rfind(b"/")
    return (kept[:slash] if slash >= 0 else b".") + b"/"


def _flycast_parent(path: str) -> str:
    """``getParentPath`` (storage.cpp:145-168) for a directory it may read: up to the last ``/``."""
    slash = path.rfind("/")
    if slash == 0:
        return "/"
    return "./" if slash < 0 else path[:slash]


def _flycast_entries(data: bytes, roms_dir: bytes, on_line: Callable[[], None]) -> Iterator[str]:
    """``read_m3u`` (libretro.cpp:3740-3792): its discs as it reads them, every ``fgets`` piece a line.

    A ``#`` in the first column skips the line; the line ends at its first
    ``\\r``, then its first ``\\n``; one leading and one trailing ``"`` go. An
    entry that is not absolute joins ``g_roms_dir``, and the disc's path is
    cut to 4095 bytes. A line left empty is no disc — the trailing-quote test
    then reads the byte before the buffer, which decides nothing about it.
    """
    for piece in _gets(data, _FLYCAST_LINE):
        on_line()
        line = _c_string(piece)
        if line[:1] == b"#":
            continue
        line = line.split(b"\r", 1)[0].split(b"\n", 1)[0]
        line = line.removeprefix(b'"')
        line = line.removesuffix(b'"')
        if line:
            name = line if line[:1] == b"/" else roms_dir + line
            yield _path(name[: _FLYCAST_LINE - 1])


class _CppStream:
    """An ``std::istream`` over a sheet's bytes in the C locale, as far as Flycast's parsers use one (libstdc++).

    ``failed`` is the stream's fail state: once set, every extraction fails
    and leaves its target as it was.
    """

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.at = 0
        self.failed = False

    def _sentry(self) -> bool:
        """``skipws`` and the sentry: whitespace skipped, and a stream at its end fails."""
        if not self.failed:
            self.at = _run_end(_SPACE_RUN, self.data, self.at)
            self.failed = self.at >= len(self.data)
        return not self.failed

    def char(self) -> bytes | None:
        """``>> char``: the next byte that is no space, or ``None`` where the extraction fails."""
        if not self._sentry():
            return None
        self.at += 1
        return self.data[self.at - 1 : self.at]

    def word(self) -> bytes | None:
        """``>> std::string``: the next run of bytes that are no space, or ``None`` where the extraction fails."""
        if not self._sentry():
            return None
        start = self.at
        self.at = _run_end(_TOKEN_RUN, self.data, start)
        return self.data[start : self.at]

    def quoted(self) -> bytes | None:
        """``noskipws`` reads up to the closing ``"``; ``None`` where none comes, which the parsers loop on forever."""
        end = self.data.find(b'"', self.at)
        if end < 0:
            return None
        name = self.data[self.at : end]
        self.at = end + 1
        return name

    def number(self, bits: int, *, signed: bool) -> int | None:
        """``>> u32`` or ``>> s32``: ``None`` where the sentry fails and the target keeps its value.

        A field with no digit stores 0 and fails the stream; one past the
        type's range stores the nearest limit and fails it. An unsigned field
        takes a ``-`` and wraps.
        """
        if not self._sentry():
            return None
        negative = self.data[self.at : self.at + 1] == b"-"
        if self.data[self.at : self.at + 1] in (b"+", b"-"):
            self.at += 1
        start = self.at
        self.at = _run_end(_DIGIT_RUN, self.data, start)
        if self.at == start:
            self.failed = True
            return 0
        value, fits = _cpp_value(self.data[start : self.at], negative, bits, signed=signed)
        self.failed = not fits
        return value


def _run_end(run: re.Pattern[bytes], data: bytes, at: int) -> int:
    """Where a run of one byte class starting at *at* ends."""
    match = run.match(data, at)
    return at if match is None else match.end()


def _cpp_value(digits: bytes, negative: bool, bits: int, *, signed: bool) -> tuple[int, bool]:
    """``num_get``'s stored value for a field's digits, and whether it fit: past the range, the nearest limit."""
    digits = digits.lstrip(b"0") or b"0"
    highest = (1 << bits) - 1
    if signed:
        highest = (1 << (bits - 1)) - (0 if negative else 1)
    if len(digits) > len(str(highest)) or int(digits) > highest:
        return (-highest if signed and negative else highest), False
    value = -int(digits) if negative else int(digits)
    return (value if signed else value % (1 << bits)), True


def _cpp_lines(data: bytes) -> Iterator[bytes]:
    """``std::getline``'s lines: each ended by ``\\n``, the last one only where it holds a byte."""
    start = 0
    while start < len(data):
        end = data.find(b"\n", start)
        if end < 0:
            yield data[start:]
            return
        yield data[start:end]
        start = end + 1


class _FlycastReader:
    """Flycast's open of one launched file (retro_load_game, libretro.cpp:2100-2307 at 1dac369)."""

    def __init__(self, machine: Machine, profile: FlycastProfile, label: str, launched: str) -> None:
        self.machine = machine
        self.profile = profile
        self.walk = _Walk(machine, label, profile.name, launched)

    def read(self, path: str) -> None:
        self.walk.source(
            f"entry {self.walk.label!r}: {self.profile.name} opens {path} through retro_load_game "
            "(libretro.cpp:2100-2307)"
        )
        if _flycast_playlist(path):
            self._playlist(path)
        elif _flycast_extension(path) == "m3u":
            self.walk.fail(
                _AT_LAUNCH,
                CAVEAT_OPENS_FORMAT_REFUSED,
                path,
                "takes a playlist only where the file's name, cut to its first 127 bytes, ends in .m3u or .M3U "
                "(libretro.cpp:2117, :2159-2187), and opens no disc image of this name's extension",
            )
        else:
            self._disc(path, _AT_LAUNCH)

    def _playlist(self, path: str) -> None:
        """``read_m3u``, then its first disc at launch; every other one is opened only on a disc switch."""
        text = self.walk.text(path, playlist=True)
        if text is None:
            self.walk.unsure(_AT_LAUNCH, CAVEAT_OPENS_FILE_UNREAD, path, _UNREAD)
            return
        entries = _flycast_entries(_bytes(text), _flycast_roms_dir(path), lambda: self.walk.step(path))
        first = next(entries, None)
        if first is None:
            self.walk.fail(_AT_LAUNCH, CAVEAT_OPENS_SHEET_REFUSED, path,
                           "reads no disc from this playlist (read_m3u, libretro.cpp:3740-3792)")
            return
        if not self._launch_entry(first, path):
            return
        for entry in entries:
            self._switch_entry(entry, path)

    def _launch_entry(self, entry: str, playlist: str) -> bool:
        """``Emulator::loadGame``: the disc stated, its platform picked by name, then opened (emulator.cpp:547-621)."""
        present = self.walk.list(entry, HOW_NAMED, playlist)
        if present is None:
            self.walk.unsure(_AT_LAUNCH, CAVEAT_OPENS_FILE_UNREAD, entry, _UNTOLD)
            return False
        if not present:
            self.walk.fail(_AT_LAUNCH, CAVEAT_OPENS_FILE_MISSING, entry, _MISSING)
            return False
        if _flycast_extension(entry[entry.rfind("/") + 1 :]) in _FLYCAST_ARCADE or _flycast_extension(entry) == "elf":
            self.walk.unsure(_AT_LAUNCH, CAVEAT_OPENS_FORMAT_UNREAD, entry,
                             "{file} is loaded as an arcade cartridge or an executable, which this reading does "
                             "not follow")
            return False
        return self._disc(entry, _AT_LAUNCH)

    def _switch_entry(self, entry: str, playlist: str) -> None:
        """A disc switch: ``OpenDisc`` alone (``gdr::insertDisk``, common.cpp:366-369), a failure the launch survives.

        A disc of no extension a driver takes is refused whether it is there or not.
        """
        present = self.walk.list(entry, HOW_NAMED, playlist)
        if _flycast_driver(entry) is not None and present is None:
            self.walk.unsure(_ON_SWITCH, CAVEAT_OPENS_FILE_UNREAD, entry, _UNTOLD)
        elif _flycast_driver(entry) is not None and not present:
            self.walk.fail(_ON_SWITCH, CAVEAT_OPENS_FILE_MISSING, entry, _MISSING)
        else:
            self._disc(entry, _ON_SWITCH)

    def _disc(self, path: str, mode: _Mode) -> bool:
        """``OpenDisc``: the first driver whose extension matches opens the image (common.cpp:86-97)."""
        driver = _flycast_driver(path)
        if driver == "gdi":
            return _FlycastGdi(self, path, mode).open()
        if driver == "cue":
            return _FlycastCue(self, path, mode).open()
        if driver is not None:
            self.walk.unsure(mode, CAVEAT_OPENS_FORMAT_UNREAD, path, _NOT_FOLLOWED)
            return False
        self.walk.fail(mode, CAVEAT_OPENS_FORMAT_REFUSED, path,
                       "opens no disc image of this name's extension (OpenDisc's \"Unknown disk format\", "
                       "common.cpp:86-97)")
        return False

    def sheet(self, path: str, mode: _Mode, limit: int, cited: str) -> bytes | None:
        """A ``.gdi``'s or ``.cue``'s bytes up to its first NUL, as the ``istringstream`` over its buffer holds them.

        A sheet of *limit* bytes or more is refused before it is read.
        """
        size = self.machine.file_size(path)
        if size is None or size < limit:
            text = self.walk.text(path, playlist=False)
            if text is None:
                self.walk.unsure(mode, CAVEAT_OPENS_FILE_UNREAD, path, "{file} could not be read as a disc sheet")
                return None
            data = _bytes(text)
            if len(data) < limit:
                return _c_string(data)
        self.walk.fail(mode, CAVEAT_OPENS_SHEET_REFUSED, path,
                       f"refuses {{file}}, a disc sheet of {limit} bytes or more ({cited})")
        return None

    def track(self, sheet: str, parent: str, name: bytes, mode: _Mode) -> str | None:
        """One track file opened: the sheet's directory, ``/`` and the name, absolute or not (storage.cpp:170-174)."""
        path = f"{parent}/{_path(name)}"
        present = self.walk.list(path, HOW_NAMED, sheet)
        if present is None:
            self.walk.unsure(mode, CAVEAT_OPENS_FILE_UNREAD, path, _UNTOLD)
            return None
        if not present:
            self.walk.fail(mode, CAVEAT_OPENS_FILE_MISSING, path, _MISSING)
            return None
        return path


class _FlycastGdi:
    """``load_gdi`` (gdi.cpp:6-116): a track count, then ``TRACK FAD CTRL SSIZE <name> OFFSET`` per track.

    A track is opened only where its sector size is not 0; a sheet of fewer
    than three tracks is refused after its files opened.
    """

    def __init__(self, reader: _FlycastReader, path: str, mode: _Mode) -> None:
        self.reader = reader
        self.walk = reader.walk
        self.path = path
        self.mode = mode
        self.parent = _flycast_parent(path)

    def open(self) -> bool:
        data = self.reader.sheet(self.path, self.mode, _GDI_LIMIT, "\"GDI file too big\", gdi.cpp:18-24")
        if data is None:
            return False
        stream = _CppStream(data)
        declared = stream.number(32, signed=False) or 0
        if declared == 0:
            self.walk.fail(self.mode, CAVEAT_OPENS_SHEET_REFUSED, self.path,
                           "refuses {file}: it declares no track (\"empty or invalid GDI file\", gdi.cpp:32-35)")
            return False
        for track in range(declared):
            if track >= MAX_TRACKS:
                raise _Bounded(self.path)
            self.walk.step(self.path)
            if not self._track(stream):
                return False
        if declared < _FLYCAST_LEAST_TRACKS:
            self.walk.fail(self.mode, CAVEAT_OPENS_SHEET_REFUSED, self.path,
                           "refuses {file}: it holds fewer than three tracks (gdi.cpp:107-110)")
            return False
        return True

    def _track(self, stream: _CppStream) -> bool:
        """One track's fields (gdi.cpp:46-105); a stream already failed leaves ``last`` unset when it is tested."""
        for _field in range(3):
            stream.number(32, signed=False)
        size = stream.number(32, signed=False)
        last = None if stream.failed else stream.char()
        if last is None:
            self.walk.unsure(self.mode, CAVEAT_OPENS_CONTENT_UNREAD, self.path, _UNDEFINED)
            return False
        if last == b'"':
            name = stream.quoted()
            if name is None:
                self.walk.fail(self.mode, CAVEAT_OPENS_SHEET_REFUSED, self.path, _UNENDING)
                return False
        else:
            name = last + (stream.word() or b"")
        stream.number(32, signed=True)
        return size == 0 or self.reader.track(self.path, self.parent, name, self.mode) is not None


class _FlycastCue:
    """``cue_parse`` (cue.cpp:46-250): a line at a time, each track's file opened at its ``INDEX 01``.

    The ``FILE`` type is never checked; the track mode is, after the file
    opened. A quoted name is added to the one before it until an ``INDEX 01``
    clears it. Without a ``REM SESSION`` a sheet needs three tracks.
    """

    def __init__(self, reader: _FlycastReader, path: str, mode: _Mode) -> None:
        self.reader = reader
        self.walk = reader.walk
        self.path = path
        self.mode = mode
        self.parent = _flycast_parent(path)
        self.name = b""
        self.kind = b""
        self.session = 0
        self.tracks = 0

    def open(self) -> bool:
        data = self.reader.sheet(self.path, self.mode, _FLYCAST_CUE_LIMIT, "\"CUE file too big\", cue.cpp:62-68")
        if data is None:
            return False
        for line in _cpp_lines(data):
            self.walk.step(self.path)
            ended = self._line(_CppStream(line))
            if ended is not None:
                return ended
        if self.tracks == 0:
            self.walk.fail(self.mode, CAVEAT_OPENS_SHEET_REFUSED, self.path,
                           "finds no track in {file} (cue.cpp:224-228)")
            return False
        if self.session == 0 and self.tracks < _FLYCAST_LEAST_TRACKS:
            self.walk.fail(self.mode, CAVEAT_OPENS_SHEET_REFUSED, self.path,
                           "finds fewer than three tracks in {file} and no REM SESSION line (cue.cpp:230-235)")
            return False
        return True

    def _line(self, stream: _CppStream) -> bool | None:
        """One line: ``None`` to read on, or whether the open stands where the sheet's read ends here."""
        command = stream.word()
        if command == b"FILE":
            return self._file(stream)
        if command == b"INDEX":
            return self._index(stream)
        if command == b"REM":
            return self._rem(stream)
        if command == b"TRACK" and stream.number(32, signed=False) is not None and not stream.failed:
            self.kind = stream.word() or self.kind
        return None

    def _rem(self, stream: _CppStream) -> bool | None:
        if stream.word() != b"SESSION":
            return None
        session = stream.number(32, signed=False)
        if session is None:
            self.walk.unsure(self.mode, CAVEAT_OPENS_CONTENT_UNREAD, self.path, _UNDEFINED)
            return False
        self.session = session or self.session
        return None

    def _file(self, stream: _CppStream) -> bool | None:
        last = stream.char()
        if last is None:
            self.walk.unsure(self.mode, CAVEAT_OPENS_CONTENT_UNREAD, self.path, _UNDEFINED)
            return False
        if last == b'"':
            quoted = stream.quoted()
            if quoted is None:
                self.walk.fail(self.mode, CAVEAT_OPENS_SHEET_REFUSED, self.path, _UNENDING)
                return False
            self.name += quoted
        else:
            word = stream.word()
            self.name = last + (self.name if word is None else word)
        return None

    def _index(self, stream: _CppStream) -> bool | None:
        number = stream.number(32, signed=False)
        if number is None:
            self.walk.unsure(self.mode, CAVEAT_OPENS_CONTENT_UNREAD, self.path, _UNDEFINED)
            return False
        if number != 1:
            return None
        path = self.reader.track(self.path, self.parent, self.name, self.mode)
        if path is None:
            return False
        if self.kind not in _FLYCAST_MODES:
            kind = _path(self.kind)
            self.walk.fail(self.mode, CAVEAT_OPENS_TRACK_REFUSED, path,
                           f"takes no track of mode {kind!r} (getSectorSize, cue.cpp:25-44, :188-194)", kind)
            return False
        self.tracks += 1
        if self.tracks > MAX_TRACKS:
            raise _Bounded(self.path)
        self.name = self.kind = b""
        return None


# ── Genesis Plus GX ────────────────────────────────────────────────────────

# What the core keeps of a path: retro_load_game's content_path and
# g_rom_dir, and cdd_load's copy fname, which also holds one byte past them it
# never sets (libretro.c:154, :3423; cdd.c:392, :583-584 at 46a5521).
_GPGX_PATH: Final = 255
# cdd_load's line buffer (cdd.c:393, read through rfgets), and the M3U reader's
# (libretro.c:3570-3571).
_GPGX_LINE: Final = 128
_GPGX_LIST_LINE: Final = 512
# MAX_DISKS (libretro.c:2762), and the tracks a sheet mounts (cdd.c:975-976).
_GPGX_DISCS: Final = 4
_GPGX_TRACKS: Final = 99
# The largest file the core loads as a cartridge (MAXROMSIZE, loadrom.h:44).
_GPGX_ROM_LIMIT: Final = 10 << 20
# RetroArch's directory buffer the content's directory is cut to (DIR_MAX_LENGTH
# in retro_miscellaneous.h at a79435a).
_RETROARCH_DIR: Final = 1023
_GPGX_MAGIC: Final = b"SEGADISCSYSTEM"
_GPGX_SYNC: Final = b"\x00" + b"\xff" * 10 + b"\x00"
_GPGX_INDEX_00: Final = (b"INDEX 00 %02d:%02d:%02d", b"INDEX 0 %02d:%02d:%02d")
_GPGX_INDEX_01: Final = (b"INDEX 01 %02d:%02d:%02d", b"INDEX 1 %02d:%02d:%02d")
_C_WS: Final = b" \t\n\v\f\r"
_GPGX_LATER = _Mode(stops=False, decisive=False, goes_on="the disc mounts with the tracks read before it")
_GPGX_CARTRIDGE = _Mode(
    stops=False,
    decisive=False,
    goes_on="the core loads the file it was handed as a cartridge whatever this sheet holds",
)
_GPGX_AS_CARTRIDGE = "loads the cue sheet itself as a cartridge (cdd.c:1286-1303, loadrom.c:579-597)"
# A disc switch runs cdd_load alone and fails where no track mounts.
_GPGX_SWITCH_FAILS = "the disc switch fails (disk_set_image_index, libretro.c:2817-2820)"
_GPGX_CRASH = (
    "reaches a line of {file} that reads or seeks on the stream of a first track no FILE line opened, and crashes "
    "(cdd.c:826-836, :949-951)"
)
_GPGX_MODES = (b"MODE1/2048", b"MODE1/2352", b"MODE2/2352")


def _gpgx_playlist(path: str) -> bool:
    """retro_load_game's ``m3u`` test (libretro.c:3564) of the extension RetroArch hands over.

    RetroArch lowercases the extension after the name's last ``.``
    (task_content.c:546-554 at a79435a), and the core keeps seven characters
    of it (``content_ext[8]``, libretro.c:3446-3447), a cut that leaves no
    longer extension equal to ``m3u`` for its ``strcmp``.
    """
    name = path[path.rfind("/") + 1 :]
    dot = name.rfind(".")
    return dot >= 0 and _ascii_lower(name[dot + 1 :]) == "m3u"


def _gpgx_sheet_route(path: str) -> bool:
    """``cdd_load``'s cue test: the path's last three characters ``cue`` or ``CUE`` (cdd.c:587)."""
    return _bytes(path)[-3:] in (b"cue", b"CUE")


def _gpgx_reads(path: str) -> bool:
    """Whether this cut reads the core's open of a launched *path*: a playlist, a cue sheet, a ``.bin`` or ``.iso``."""
    name = path[path.rfind("/") + 1 :]
    dot = name.rfind(".")
    suffix = _ascii_lower(name[dot:]) if dot >= 0 else ""
    return _gpgx_playlist(path) or _gpgx_sheet_route(path) or suffix in (".bin", ".iso", ".cue")


def _gpgx_stem_cue(path: str) -> str:
    """The ``.cue`` a file that is no cue sheet looks for (cdd.c:655-659): fname to a ``.`` at byte 251 or before."""
    kept = _bytes(path)[:_GPGX_PATH]
    end = len(kept)
    while (end and (end >= len(kept) or kept[end] != 0x2E)) or end > _GPGX_PATH - 4:
        end -= 1
    return _path(kept[:end] + b".cue")


def _gpgx_base(fname: bytes) -> bytes:
    """What a ``FILE`` name is written after (cdd.c:697-700): fname to its last ``/`` or ``\\`` past its first byte."""
    cut = max(fname.rfind(b"/"), fname.rfind(b"\\"))
    return fname[: cut + 1] if cut > 0 else b""


def _gpgx_name(rest: bytes, stop: bytes, room: int) -> tuple[bytes, bytes | None]:
    """A ``FILE`` name copied up to *stop* or *room* bytes (cdd.c:710-723), and the line after it.

    The copy does not stop at the line's end: a name no *stop* ends within it
    runs on into the buffer's stale bytes, so the C string holds the rest of
    the line and what follows it — the type it is read for — is unknown
    (``None``).
    """
    end = rest.find(stop)
    if 0 <= end <= room:
        return rest[:end], rest[end:]
    if len(rest) >= room:
        return rest[:room], rest[room:]
    return rest, None


def _gpgx_rom_dir(playlist: str) -> bytes | None:
    """``g_rom_dir``: RetroArch's directory of the content, cut to 1023 then 255 bytes — ``None`` for none at all.

    ``fill_pathname_parent_dir`` and the trailing ``/`` removed
    (task_content.c:592-611 at a79435a); a file in ``/`` leaves no directory,
    which the core copies from a null pointer.
    """
    kept = _bytes(playlist)[:_RETROARCH_DIR]
    slash = kept.rfind(b"/")
    directory = kept[:slash] if slash >= 0 else b"."
    return directory[:_GPGX_PATH] or None


def _gpgx_entries(data: bytes, directory: bytes, on_line: Callable[[], None]) -> tuple[list[str], bool]:
    """The M3U reader (libretro.c:3563-3606): up to four discs, and whether a line past the fourth was left unread.

    A ``#`` in the first column skips the line; one ``\\n`` then one ``\\r``
    go from its end, and what is left — nothing included — joins the
    directory with a ``/``, absolute or not.
    """
    entries: list[str] = []
    for piece in _gets(data, _GPGX_LIST_LINE):
        on_line()
        if len(entries) == _GPGX_DISCS:
            return entries, True
        line = _c_string(piece)
        if line[:1] == b"#" or not line:
            continue
        entries.append(_path(directory + b"/" + line.removesuffix(b"\n").removesuffix(b"\r")))
    return entries, False


def _skip_c_spaces(text: bytes, at: int) -> int:
    """Where *text* goes on past the C spaces at *at*."""
    while at < len(text) and text[at] in _C_WS:
        at += 1
    return at


def _scan_two_digits(text: bytes, at: int) -> int | None:
    """``%02d`` past its leading spaces: a sign and digits within two bytes — where they end, ``None`` for no digit."""
    sign = 1 if text[at : at + 1] in (b"+", b"-") else 0
    end = at + sign
    while end < min(at + 2, len(text)) and text[end] in _DIGITS:
        end += 1
    return None if end == at + sign else end


def _scanf_directives(fmt: bytes) -> Iterator[bytes]:
    """A format's directives: ``%02d``, a space, or one literal byte."""
    index = 0
    while index < len(fmt):
        size = 4 if fmt.startswith(b"%02d", index) else 1
        yield fmt[index : index + size]
        index += size


def _scanf_step(text: bytes, at: int, directive: bytes) -> int | None:
    """One directive matched at *at*: where the input goes on, -1 where it ended first, ``None`` on a mismatch."""
    if directive == b" ":
        return _skip_c_spaces(text, at)
    if directive == b"%02d":
        at = _skip_c_spaces(text, at)
    if at >= len(text):
        return -1
    if directive == b"%02d":
        return _scan_two_digits(text, at)
    return at + 1 if text[at] == directive[0] else None


def _scanf_count(text: bytes, fmt: bytes) -> int:
    """``sscanf``'s return for a format of literal bytes, spaces and ``%02d`` alone.

    The conversions made, or -1 where the input ends before the first.
    """
    at: int | None = 0
    count = 0
    for directive in _scanf_directives(fmt):
        at = _scanf_step(text, at, directive)
        if at is None:
            return count
        if at < 0:
            return count or -1
        count += directive == b"%02d"
    return count


def _gpgx_track_index(line: bytes) -> bool:
    """Whether a line ends a track: ``INDEX 01`` where no earlier test of the chain took it (cdd.c:802-894)."""
    if b"REM LOOP" in line or b"REM NOLOOP" in line:
        return False
    if any(_scanf_count(line, fmt) == 3 for fmt in _GPGX_INDEX_00):
        return False
    return any(_scanf_count(line, fmt) == 3 for fmt in _GPGX_INDEX_01)


def _gpgx_crashes(line: bytes) -> bool:
    """Whether a line reads or seeks on the first track's stream, a crash while no ``FILE`` has opened it.

    A ``TRACK`` line naming ``MODE1/2048``, ``MODE1/2352`` or ``MODE2/2352``,
    the modes that set a sector size, reads the header through it
    (cdd.c:802-837); no other mode does. An ``INDEX 01`` seeks to its end
    (:893-951).
    """
    track = _scanf_count(line, b"TRACK %02d") != 0 and any(mode in line for mode in _GPGX_MODES)
    return track or _gpgx_track_index(line)


def _switching(mode: _Mode) -> bool:
    """Whether *mode* is a disc switch, where Genesis Plus GX runs ``cdd_load`` alone and loads no cartridge."""
    return mode is _ON_SWITCH


def _gpgx_no_track(mode: _Mode) -> str:
    """What follows where no track of a disc mounts: the switch fails, or at launch the sheet loads as a cartridge."""
    return _GPGX_SWITCH_FAILS if _switching(mode) else f"the core {_GPGX_AS_CARTRIDGE}"


def _gpgx_may_be_cd(header: bytes) -> bool:
    """Whether a file's first 16 bytes may make it a CD data track (cdd.c:593-620).

    A file shorter than the test leaves stale bytes in the buffer, so a
    prefix that agrees with either pattern so far may still pass.
    """
    magic = header[:14] == _GPGX_MAGIC[: len(header[:14])]
    sync = header[:12] == _GPGX_SYNC[: len(header[:12])] and (len(header) < 16 or header[15] in (1, 2))
    return magic or sync


class _GpgxReader:
    """Genesis Plus GX's open of one launched file (retro_load_game, libretro.c:3413-3646 at 46a5521)."""

    def __init__(self, machine: Machine, profile: GpgxProfile, label: str, launched: str) -> None:
        self.machine = machine
        self.profile = profile
        self.walk = _Walk(machine, label, profile.name, launched)

    def read(self, path: str) -> None:
        self.walk.source(
            f"entry {self.walk.label!r}: {self.profile.name} opens {path} through retro_load_game and cdd_load "
            "(libretro.c:3413-3646, cdd.c:390-1304)"
        )
        if len(_bytes(path)) > _GPGX_PATH:
            self.walk.fail(_AT_LAUNCH, CAVEAT_OPENS_PATH_TOO_LONG, path,
                           "keeps the first 255 bytes of {file} (content_path, libretro.c:3423, :3463) and opens "
                           "that other path")
        elif _gpgx_playlist(path):
            self._playlist(path)
        else:
            self._load(path, _AT_LAUNCH)

    def list(self, path: str, how: OpenedHow, by: str) -> bool | None:
        """List *path*; one that is not absolute is opened against a working directory nothing here records."""
        return self.walk.list(path, how, by, None, known=not path.startswith("/"))

    def _playlist(self, path: str) -> None:
        """The M3U reader, the first disc loaded at launch, the others only on a disc switch (libretro.c:2801-2824)."""
        text = self.walk.text(path, playlist=True)
        if text is None:
            self.walk.unsure(_AT_LAUNCH, CAVEAT_OPENS_FILE_UNREAD, path, _UNREAD)
            return
        directory = _gpgx_rom_dir(path)
        if directory is None:
            self.walk.unsure(_AT_LAUNCH, CAVEAT_OPENS_CONTENT_UNREAD, path,
                             "{file} lies in the root directory, which RetroArch hands the core as no directory")
            return
        entries, ignored = _gpgx_entries(_bytes(text), directory, lambda: self.walk.step(path))
        if ignored:
            self.walk.caveat(
                CAVEAT_OPENS_ENTRY_IGNORED,
                path,
                f"{self.profile.name} reads no more than four discs from {{file}} (MAX_DISKS, libretro.c:2762, "
                ":3571), so its lines past the fourth disc are never read",
                limit=_GPGX_DISCS,
            )
        if not entries:
            self.walk.fail(_AT_LAUNCH, CAVEAT_OPENS_SHEET_REFUSED, path,
                           "reads no disc from this playlist (libretro.c:3609-3633)")
            return
        if not self._entry(entries[0], path, _AT_LAUNCH):
            return
        for entry in entries[1:]:
            self._entry(entry, path, _ON_SWITCH)

    def _entry(self, entry: str, playlist: str, mode: _Mode) -> bool:
        """One disc of the playlist, loaded as ``load_rom`` loads any content (loadrom.c:554-617)."""
        present = self.list(entry, HOW_NAMED, playlist)
        if present is None:
            self.walk.unsure(mode, CAVEAT_OPENS_FILE_UNREAD, entry, _UNTOLD)
            return False
        if not present:
            self.walk.fail(mode, CAVEAT_OPENS_FILE_MISSING, entry, _MISSING)
            return False
        return self._load(entry, mode)

    def _load(self, path: str, mode: _Mode) -> bool:
        """``cdd_load``'s three routes: a CHD, a cue sheet, and any other file (cdd.c:413-660)."""
        tail = _bytes(path)[-3:]
        if tail in (b"chd", b"CHD"):
            self.walk.unsure(mode, CAVEAT_OPENS_FORMAT_UNREAD, path, _NOT_FOLLOWED)
            return False
        if not _gpgx_sheet_route(path):
            return self._image(path, mode)
        if len(_bytes(path)) >= _GPGX_PATH:
            # The sheet itself opens whole (cdd.c:405) — a playlist's disc keeps its
            # full path (libretro.c:3594-3598), a launched one 255 bytes of it
            # (:3423, :3463) — but the copy its track paths are built from does not.
            # A disc switch that does not mount leaves the launch as it was.
            self.walk.unsure(mode, CAVEAT_OPENS_PATH_TOO_LONG, path,
                             "{file} is 255 bytes or longer, and the copy cdd_load builds its track paths from "
                             "keeps 255 of them and one byte it never sets (cdd.c:583-584)",
                             effect=EFFECT_GOES_ON if _switching(mode) else EFFECT_UNESTABLISHED)
            return False
        return _GpgxSheet(self, path, mode).open()

    def _image(self, path: str, mode: _Mode) -> bool:
        """A file that is no cue sheet: its first bytes decide CD or cartridge, then the cue of its name is opened."""
        cue = _gpgx_stem_cue(path)
        present = self.list(cue, HOW_NAME_DERIVED, path)
        if present is None:
            self.walk.caveat(CAVEAT_OPENS_FILE_UNREAD, cue, _UNTOLD_THERE)
        header = self._header(path)
        if header is None or _gpgx_may_be_cd(header):
            return self._either(path, mode, cue_absent=present is False)
        if present:
            # No cartridge loads on a disc switch, and a crash stops the core whichever it is.
            sheet = _GpgxSheet(self, cue, mode if _switching(mode) else _GPGX_CARTRIDGE, cartridge=True, crash=mode)
            sheet.open()
            if sheet.crashed:
                return False
        if _switching(mode):
            self.walk.fail(mode, CAVEAT_OPENS_FORMAT_REFUSED, path,
                           "takes a cue sheet only by a cue or CUE suffix and finds no CD data track in {file}: a "
                           "disc switch to it mounts the tracks of the .cue of its name where that holds any, and "
                           f"otherwise {_GPGX_SWITCH_FAILS}")
        else:
            self.walk.fail(mode, CAVEAT_OPENS_FORMAT_REFUSED, path,
                           "takes a cue sheet only by a cue or CUE suffix and loads {file} as a cartridge "
                           "(cdd.c:586-653, loadrom.c:579-597)")
        return False

    def _either(self, path: str, mode: _Mode, *, cue_absent: bool) -> bool:
        """A file whose first bytes, unread here, make it a CD data track or a cartridge.

        Without a ``.cue`` of its name both load: a data track mounts alone,
        with simulated audio tracks (cdd.c:620-645, :1006-1281, :1286-1300), and
        a cartridge of 1 to 10 MiB loads as one (loadrom.c:593-617,
        load_archive's ``MAXROMSIZE``, libretro.c:375-390, :420-428). So the bytes
        decide what the content is, not whether it launches — stated, and not
        deciding the take. Where a ``.cue`` is there, or a cartridge that size
        would not load, they decide what opens or whether it launches.
        """
        if _switching(mode):
            self.walk.unsure(mode, CAVEAT_OPENS_CONTENT_UNREAD, path,
                             "{file} is a CD data track or not by its first bytes, which atlas does not read, and "
                             "a disc switch to it mounts no cartridge: it fails where neither the file nor the .cue "
                             "of its name mounts a track (disk_set_image_index, libretro.c:2817-2820)")
            return False
        size = self.machine.file_size(path)
        if cue_absent and size is not None and 0 < size <= _GPGX_ROM_LIMIT:
            self.walk.caveat(
                CAVEAT_OPENS_CONTENT_UNREAD,
                path,
                f"{self.profile.name} takes {{file}} as a CD data track or as a cartridge by its first bytes, which "
                "atlas does not read, and loads it either way; as a data track it also looks for audio tracks of "
                "its name (<stem>01.ogg, <stem> 01.wav and the like, cdd.c:1006-1176) and a .sub (:1294-1296), "
                "which this reading does not list",
            )
            return True
        self.walk.unsure(mode, CAVEAT_OPENS_CONTENT_UNREAD, path,
                         "{file} is taken as a CD data track or a cartridge by its first bytes, which atlas "
                         "does not read")
        return False

    def _header(self, path: str) -> bytes | None:
        """The first 16 bytes of a file atlas reads as text (a ``.cue`` or ``.m3u`` by any case), else ``None``."""
        name = path[path.rfind("/") + 1 :]
        if "." not in name or _ascii_lower(name[name.rfind(".") :]) not in (".cue", ".m3u"):
            return None
        text = self.walk.text(path, playlist=False)
        return None if text is None else _bytes(text)[:16]


class _GpgxSheet:
    """``cdd_load``'s parse of one cue sheet (cdd.c:662-1005), its files opened as its ``FILE`` lines reach them.

    A line is a 127-byte piece; only spaces lead it. A ``FILE`` name is
    written after the last separator of the path built before it, cut where
    the whole reaches 255 bytes; a file that will not open ends the parse. A
    type other than ``BINARY`` or ``MOTOROLA`` is decided by the file's bytes.
    The ``.sub`` looked for is the last path built with its last four bytes
    replaced (cdd.c:1294-1296).
    """

    def __init__(
        self, reader: _GpgxReader, path: str, mode: _Mode, *, cartridge: bool = False, crash: _Mode | None = None
    ) -> None:
        self.reader = reader
        self.walk = reader.walk
        self.path = path
        self.mode = mode
        self.cartridge = cartridge
        # How a crash counts: as the load this sheet is read for, a cartridge's too.
        self.crash = crash or mode
        self.crashed = False
        self.fname = _bytes(path)
        self.tracks = 0
        self.stopped = False
        # Whether a FILE line opened the first track's stream (tracks[0].fd).
        self.first_open = False

    def open(self) -> bool:
        """Whether a track mounts, as far as atlas reads it."""
        text = self.walk.text(self.path, playlist=False)
        if text is None:
            self.walk.unsure(self.mode, CAVEAT_OPENS_FILE_UNREAD, self.path, _NOT_A_SHEET)
            return False
        for piece in _gets(_bytes(text), _GPGX_LINE):
            self.walk.step(self.path)
            if not self._line(_c_string(piece).lstrip(b" ")):
                break
        if self.tracks == 0:
            if not (self.stopped or self.cartridge):
                what = f"finds no track in {{file}}, so {_gpgx_no_track(self.mode)}"
                self.walk.fail(self.mode, CAVEAT_OPENS_SHEET_REFUSED, self.path, what)
            return False
        self._sub()
        return True

    def _line(self, line: bytes) -> bool:
        """One piece: whether the parse reads on."""
        if line.startswith(b"FILE"):
            return self._file(line)
        if self.tracks == 0 and not self.first_open and _gpgx_crashes(line):
            self.walk.fail(self.crash, CAVEAT_OPENS_SHEET_REFUSED, self.path, _GPGX_CRASH)
            self.stopped = self.crashed = True
            return False
        if _gpgx_track_index(line):
            self.tracks += 1
            return self.tracks < _GPGX_TRACKS
        return True

    def _file(self, line: bytes) -> bool:
        uri = b"://" in line
        base = b"" if uri else _gpgx_base(self.fname)
        rest = line[4:].lstrip(b" ")
        quoted = rest[:1] == b'"'
        name, after = _gpgx_name(rest[1:] if quoted else rest, b'"' if quoted else b" ", _GPGX_PATH - len(base))
        self.fname = base + name
        path = _path(self.fname)
        present = self.walk.list(path, HOW_NAMED, self.path, None, known=uri or not path.startswith("/"))
        mode = self.mode if self.tracks == 0 or self.cartridge else _GPGX_LATER
        if present is None:
            self.walk.unsure(mode, CAVEAT_OPENS_FILE_UNREAD, path, _UNTOLD)
            self.stopped = True
            return False
        if not present:
            first = self.tracks == 0 and not self.cartridge
            what = f"opens {{file}}, which is not there, so no track mounts and {_gpgx_no_track(mode)}"
            self.walk.fail(mode, CAVEAT_OPENS_FILE_MISSING, path, what if first else _MISSING)
            self.stopped = True
            return False
        self.first_open = self.first_open or self.tracks == 0
        if after is None or (b"BINARY" not in after and b"MOTOROLA" not in after):
            self.walk.unsure(mode, CAVEAT_OPENS_CONTENT_UNREAD, path,
                             "{file} is a track the core takes by its bytes, as RIFF/WAVE or Ogg Vorbis audio, "
                             "which atlas does not read")
        return True

    def _sub(self) -> None:
        """The subcode file of the last path built (cdd.c:1294-1296)."""
        sub = _path(self.fname[:-4] + b".sub")
        present = self.reader.list(sub, HOW_NAME_DERIVED, _path(self.fname))
        if present is None:
            self.walk.caveat(CAVEAT_OPENS_FILE_UNREAD, sub, _UNTOLD_THERE)
