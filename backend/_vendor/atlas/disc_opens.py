"""The files a launched disc file opens — each emulator's own open, read step for step (issue #716).

ES-DE accepts a file by its extension alone, so a disc's ``.cue`` and its
``.bin`` tracks, and an ``.m3u`` listing several discs, all answer
``launchable``. What tells them apart is what the emulator does with each,
and that is what this module reads: the files the emulator opens through the
launched file, in the order it opens them, each either **named** by the text
of another file (a cue sheet's ``FILE``, a playlist's line) or **derived from a
name** (a ``.sbi`` beside the image, the ``<cue stem>.bin`` a cue's missing
first file is looked for as). Every lookup goes to the filesystem the emulator
opens it on, exactly as spelled — on Linux's own filesystems that is case
sensitive, as ``fopen`` is, and atlas asks the same filesystem.

Two readings serve this cut. Every citation is at the build atlas pins for
that emulator elsewhere — DuckStation at RetroDECK/Duckstation@1f6de046b,
SwanStation at libretro/swanstation@4d309c0, Beetle PSX at
libretro/beetle-psx-libretro@d6383bf, Beetle Saturn at ccba526, Beetle PCE at
9a301c0 and Beetle PC-FX at dd04cef — save Beetle PCE Fast, which atlas pins
nowhere else: its citations are at 7e9b257, pinned here first.

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
PCE Fast :1420-1458).

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
and a caveat is stated once per code and file — remembered by its path, or
past :data:`MAX_SHOWN` characters by the shown form and a digest, which is
charged with it. So what one reading holds is bounded three ways: the texts on
its current path of reads, at most :data:`MAX_READ_BYTES` of file bytes
together (up to four times that in memory for text outside Latin-1); its
answer with everything it remembers to build it, at most :data:`MAX_OUTPUT`
characters; and one parse at a time of at most :data:`MAX_STEPS` lines.

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
    fails, a directory where a file is named, or a patch whose setting could not be read.
    """

    def __post_init__(self) -> None:
        if self.how not in OPENED_HOWS:
            raise ValueError(f"Opened: how must be one of {OPENED_HOWS}, got {self.how!r}")


# What a reading established about one entry's take on one path.
Outcome = Literal["accepts", "refuses", "unestablished"]
ACCEPTS: Outcome = "accepts"
REFUSES: Outcome = "refuses"
UNESTABLISHED: Outcome = "unestablished"

# The reasons, one code each. A refusal is the emulator's own stop; the same
# code on a launchable answer is a file the emulator goes on without.
CAVEAT_OPENS_FILE_MISSING: Final = "opens-file-missing"
CAVEAT_OPENS_TRACK_REFUSED: Final = "opens-track-refused"
CAVEAT_OPENS_SHEET_REFUSED: Final = "opens-sheet-refused"
CAVEAT_OPENS_FORMAT_REFUSED: Final = "opens-format-refused"
CAVEAT_OPENS_PLAYLIST_REFUSED: Final = "opens-playlist-refused"
CAVEAT_OPENS_FORMAT_UNREAD: Final = "opens-format-unread"
CAVEAT_OPENS_CONTENT_UNREAD: Final = "opens-content-unread"
CAVEAT_OPENS_FILE_UNREAD: Final = "opens-file-unread"
CAVEAT_OPENS_SETTING_UNREAD: Final = "opens-setting-unread"
CAVEAT_OPENS_BOUND_EXCEEDED: Final = "opens-bound-exceeded"
CAVEAT_OPENS_PATH_TOO_LONG: Final = "opens-path-too-long"

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
    """DuckStation's or SwanStation's open: the shared one, and the two places they part."""

    name: str
    looks_for_lsd: bool
    patches_every_image: bool


DUCKSTATION: Final = DuckProfile("DuckStation 1f6de046b", looks_for_lsd=True, patches_every_image=True)
SWANSTATION: Final = DuckProfile("SwanStation 4d309c0", looks_for_lsd=False, patches_every_image=False)

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

# The libretro cores read here, by their ``.so`` file name.
CORE_PROFILES: Final[dict[str, DuckProfile | MednafenProfile]] = {
    "swanstation_libretro.so": SWANSTATION,
    "mednafen_psx_libretro.so": BEETLE_PSX,
    "mednafen_psx_hw_libretro.so": BEETLE_PSX,
    "mednafen_saturn_libretro.so": BEETLE_SATURN,
    "mednafen_pce_libretro.so": BEETLE_PCE,
    "mednafen_pce_fast_libretro.so": BEETLE_PCE_FAST,
    "mednafen_pcfx_libretro.so": BEETLE_PCFX,
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
        self._said: set[tuple[str, str]] = set()

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
        self, code: str, path: str, message: str, kind: str | None = None, *, free: bool = False, key: str | None = None
    ) -> None:
        """One caveat about *path*, once per code and file; *kind* is the track type a refusal names (``type``).

        *message* spells the file ``{file}``, which becomes the path as shown
        (:func:`_shown`) — so a path longer than that is never copied whole.
        Each is charged to :data:`MAX_OUTPUT`, save the one statement that the
        walk stopped at a bound (*free*). A reading remembers which it stated
        by the path's :func:`_said_key`, or by *key* where only that is kept.
        """
        said = (code, key or _said_key(path))
        if said in self._said:
            return
        shown = _shown(path)
        message = message.replace("{file}", shown)
        if not free:
            self.charge(path, len(message) + len(shown) + len(self.label) + len(kind or "") + len(said[1]))
        self._said.add(said)
        if kind is None:
            self.caveats.append(Caveat(code, message, {"entry": self.label, "file": shown}))
        else:
            self.caveats.append(Caveat(code, message, {"entry": self.label, "file": shown, "type": kind}))

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
                        "launch this set", kind, key=key)
            self.refused = True
        else:
            self.caveat(code, path, f"{self.emulator} {what}, and {mode.goes_on}", kind, key=key)

    def unsure(self, mode: _Mode, code: str, path: str, what: str) -> None:
        """Something the take depends on that atlas does not read; it decides the take where the mode says so."""
        if mode.decisive:
            self.unestablished = True
        if code == CAVEAT_OPENS_FILE_UNREAD and path == self.launched:
            self.launched_unread = True
        self.caveat(code, path, f"{what} — what {self.emulator} makes of it is not established here")

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


def read_opens(
    machine: Machine,
    profile: DuckProfile | MednafenProfile,
    label: str,
    content_path: str,
    patches: PatchReader | None = None,
) -> OpensReading | None:
    """What *profile*'s emulator opens through *content_path* when the entry *label* launches it.

    ``None`` where this cut reads no open of the file's kind for that
    emulator; the entry is then judged as it was before. *patches* reads the
    setting that loads ``.ppf`` patches, asked only where a reading needs it.
    """
    if isinstance(profile, DuckProfile):
        if _duck_kind(content_path) not in _DUCK_LAUNCHED:
            return None
        reader: _DuckReader | _MednafenReader = _DuckReader(machine, profile, label, content_path, patches)
    else:
        if not _mednafen_reads(profile, content_path):
            return None
        reader = _MednafenReader(machine, profile, label, content_path)
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
            self.walk.unsure(mode, CAVEAT_OPENS_FORMAT_UNREAD, path, "{file} is a format this reading does not follow")
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
                self.walk.unsure(mode, CAVEAT_OPENS_FILE_UNREAD, path, "{file} could not be read as a cue sheet")
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
            self.walk.caveat(CAVEAT_OPENS_FILE_UNREAD, path, "whether {file} is there cannot be told")
        return present

    def _ppf(self, image: str, mode: _Mode) -> None:
        """The ``.ppf`` of the image's file name beside it, where the setting loads patches."""
        setting = self._patch_setting()
        if setting.loads is False:
            return
        slash = image.rfind("/") + 1
        ppf = image[:slash] + _replace_extension(image[slash:], "ppf")
        if setting.loads is None:
            self.walk.list(ppf, HOW_NAME_DERIVED, image, None, known=True)
            return
        if self.walk.list(ppf, HOW_NAME_DERIVED, image) and self.profile.patches_every_image:
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
            self.walk.unsure(mode, CAVEAT_OPENS_FORMAT_UNREAD, path, "{file} is a format this reading does not follow")

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
            self.walk.unsure(self.mode, CAVEAT_OPENS_FILE_UNREAD, self.path,
                             "{file} could not be read as a cue sheet")
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
            self.walk.caveat(CAVEAT_OPENS_FILE_UNREAD, sbi, "whether {file} is there cannot be told")
        elif present and self.profile.checks_sbi:
            self.walk.unsure(self.mode, CAVEAT_OPENS_CONTENT_UNREAD, sbi, "{file} is read by its bytes, which atlas "
                             "does not read, and one the core refuses ends the open")
