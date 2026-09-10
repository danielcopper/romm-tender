"""The machine seam — the one injected protocol every machine access flows through.

atlas is a resolver: it answers questions about the running machine by reading
the running machine. All of that reading goes through a :class:`Machine`, which
abstracts the machine itself — files, directories, symlinks, and the answers
only a core binary can give. In production the machine is the real one
(:class:`RealMachine`); in tests and conformance vectors it is a fixture
(:class:`FixtureMachine`): files, directories, symlinks, and core answers as
plain data describing a whole machine, including broken links, unreadable
files, and unloadable cores. One code path, two data sources, so everything
atlas concludes is provable from data — the failure states included.

Every operation reports an explicit outcome instead of collapsing failure
modes: ``read_text`` distinguishes *missing* from *unreadable* from
*invalid text*, and ``path_kind`` distinguishes a file from a directory from
an inaccessible path. The resolver needs those distinctions because the
emulators make them — RetroArch applies a configured directory only when
``path_is_directory()`` succeeds — and because health reporting must never
present a present-but-broken installation as absent or healthy.

``readlink`` exists because RetroDECK's standalone save architecture is symlinks
(``dir_prep``): the emulator-side path and the real path are two truthful
answers to different questions, and a dead link is a real state the resolver
must be able to see. ``query_core`` exists because ``library_name`` — the value
that names sort-by-core directories and the override directory — lives only in
the core binary; loading the core and asking it is the same read RetroArch
performs. A live read, never a shipped table.

``file_size`` and ``file_digest`` exist because firmware identity is checked by
content, not by name: a file present under the right name may still be the
wrong dump. ``file_size`` is the free pre-filter (one ``stat``) that settles
most mismatches before any bytes are hashed; ``file_digest`` is the paid
answer. Both return ``None`` for *cannot tell* — never a sentinel that a caller
could mistake for a real value.

Path resolution (normative, for ports): a path is walked component by component
from ``/``, the way ``path_resolution(7)`` describes and the kernel was observed
to behave. ``.`` and repeated separators are transparent; ``..`` is applied to
where the walk *landed*, so ``link/..`` leaves the link's target directory, not
the directory the link sits in; a symlink component is replaced by its target
and the walk continues. Every component the walk steps *through* must be a
directory, which is what makes the failure spellings answer as they do: a
trailing ``/`` on a regular file is ``ENOTDIR``, reported as *missing*, while
the same spelling on a directory is transparent. Lexical normalization
(``os.path.normpath``) is not this: it eats the component in front of a ``..``
even when that component is a symlink, and the kernel does the opposite.

Glob semantics (normative, for ports): patterns support ``*``, ``?`` and
``[seq]`` within one path segment; a wildcard never crosses a ``/``; a wildcard
segment never matches a name starting with ``.`` (only a segment that itself
starts with ``.`` does); a wildcard segment that is not the last one matches
directories only, following symlinks to decide; a pattern ending in ``/``
matches directories only and keeps that ``/``; a relative pattern matches
nothing, because the working directory is not a fact about the machine; matches
are returned sorted, spelled the way the pattern reached them (a file matched
through a symlinked directory keeps the link-side path).

``glob`` reports **how much of the walk it could read**, because "the directory
is empty" and "the directory could not be listed" are the same empty list
otherwise — and the second is the shape of a save directory on a card that
dropped off the bus. A pattern can need several directories, so the answer is
per-directory rather than all-or-nothing: ``matches`` carries what *was* found
and ``unreadable`` names every place the walk could not look. Not every empty
answer is a failure: a name that is not there, or a path component that is not
a directory, is a truthful negative and keeps the answer ``complete`` — only a
read that *failed* (permissions, a symlink loop, I/O) makes it ``incomplete``.
This is the one operation whose payload is partial rather than absent, and it
is why the two are separate fields instead of a status alone.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import stat as _stat
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Iterable, Literal, Mapping, NamedTuple, Protocol

from . import lha, ps2_bios, squashfs, whdload

_CORE_PROBE_TIMEOUT_SECONDS = 15
SYMLINK_HOPS = 40
"""How many symlink hops a path may take before it counts as unresolvable.

The number is the kernel's, observed rather than assumed: chains of 38, 39 and
40 links built in a scratch directory all ``stat`` and ``open`` fine, and 41
answers ``ELOOP`` (Linux 6.16, ``SYMLOOP_MAX``). So *this many* hops resolve and
the next one does not, and every resolver in atlas uses this one number — the
two machines and :func:`atlas.firmware.resolve_links` alike. A resolver that
disagreed inside some window would make every vector built on it prove nothing,
and the window would be exactly one hop wide, which is where nobody looks.
"""

# Digest algorithms the seam answers for. Closed on purpose: these are the two
# libretro-database's System.dat carries, and a port must implement exactly
# them (an open algorithm parameter would make conformance unprovable).
DIGEST_MD5 = "md5"
DIGEST_SHA1 = "sha1"
DIGEST_ALGORITHMS = (DIGEST_MD5, DIGEST_SHA1)

_DIGEST_CHUNK_BYTES = 1 << 20

ReadStatus = Literal["ok", "missing", "unreadable", "invalid-text"]
PathKind = Literal["file", "directory", "missing", "inaccessible"]
GlobStatus = Literal["complete", "incomplete"]

# An AppImage read has three more ways to fail than a plain file read, and
# each is a different claim a caller acts on differently: "not-appimage" is a
# file that exists and is not an AppImage-with-squashfs (replaced, truncated,
# some other executable); "entry-missing" is a healthy image without the asked
# entry (an upstream restructuring); "capability-missing" is this runtime
# lacking the image's codec (zstd needs a provider: one the host registered,
# Python 3.14's compression.zstd, or the backports.zstd package) — the file is
# fine, the runtime is what cannot open it, and reporting it as any file state
# would blame the machine for the process.
AppImageReadStatus = Literal[
    "ok", "missing", "unreadable", "invalid-text", "not-appimage", "entry-missing", "capability-missing"
]

READ_OK: ReadStatus = "ok"
READ_MISSING: ReadStatus = "missing"
READ_UNREADABLE: ReadStatus = "unreadable"
READ_INVALID_TEXT: ReadStatus = "invalid-text"

APPIMAGE_NOT_APPIMAGE: AppImageReadStatus = "not-appimage"
APPIMAGE_ENTRY_MISSING: AppImageReadStatus = "entry-missing"
APPIMAGE_CAPABILITY_MISSING: AppImageReadStatus = "capability-missing"

# A PS2 BIOS header read has one way to fail beyond a plain file read:
# "not-a-bios" is a file that was opened and read and fails the core's own
# test (atlas.ps2_bios) — a finding about the bytes, never a read failure,
# which is why it is its own word rather than a missing header.
Ps2BiosHeaderStatus = Literal["ok", "missing", "unreadable", "not-a-bios"]
# The plain read's three words again, typed as this read's so a result can be
# built from them; the fourth is this read's own.
PS2_BIOS_OK: Ps2BiosHeaderStatus = "ok"
PS2_BIOS_MISSING: Ps2BiosHeaderStatus = "missing"
PS2_BIOS_UNREADABLE: Ps2BiosHeaderStatus = "unreadable"
PS2_BIOS_NOT_A_BIOS: Ps2BiosHeaderStatus = "not-a-bios"

# Listing an archive has one way to fail beyond a plain file read:
# "not-archive" is a file that was opened and is not an archive of a kind
# atlas reads — a finding about the bytes, or about a suffix no reader here
# claims, never a read failure.
ArchiveStatus = Literal["ok", "missing", "unreadable", "not-archive"]
ARCHIVE_OK: ArchiveStatus = "ok"
ARCHIVE_MISSING: ArchiveStatus = "missing"
ARCHIVE_UNREADABLE: ArchiveStatus = "unreadable"
ARCHIVE_NOT_ARCHIVE: ArchiveStatus = "not-archive"

# Reading the WHDLoad slave out of an archive adds three more, and they are a
# different claim each. "no-slave" is an archive nothing names a slave out of:
# the boot script's search found none and the archive does not offer WHDLoad
# exactly one either. "ambiguous" is a listing the script's own search *met*
# candidates in and could not tell apart, where the archive holds no single
# slave to fall back on — a fact about the search, not about the archive
# being empty. "slave-unreadable" is a slave that was named and whose bytes
# do not come back: a compression method this runtime does not implement, a
# failed CRC, or bytes that are no slave. A caller states all three
# differently, and only the last says the archive had a name to give.
WhdloadSlaveStatus = Literal[
    "ok", "missing", "unreadable", "not-archive", "no-slave", "ambiguous", "slave-unreadable"
]
WHDLOAD_OK: WhdloadSlaveStatus = "ok"
WHDLOAD_MISSING: WhdloadSlaveStatus = "missing"
WHDLOAD_UNREADABLE: WhdloadSlaveStatus = "unreadable"
WHDLOAD_NOT_ARCHIVE: WhdloadSlaveStatus = "not-archive"
WHDLOAD_NO_SLAVE: WhdloadSlaveStatus = "no-slave"
WHDLOAD_AMBIGUOUS: WhdloadSlaveStatus = "ambiguous"
WHDLOAD_SLAVE_UNREADABLE: WhdloadSlaveStatus = "slave-unreadable"

# The archive suffixes this seam reads, and which reader each takes. UAE
# accepts both LhA spellings (sources/src/zfile.c:1483-1484 at 0043cf9), and
# ``.7z`` is deliberately absent: no reader for it ships in a runtime atlas
# may assume, so an archive in that format is answered as one atlas does not
# read rather than guessed at. A zip's *listing* needs no codec at all, and a
# member's bytes need whatever compressed them — a runtime without ``zlib``
# raises where a deflated member is read, which the slave read reports as a
# member it could not get back rather than as a broken archive.
ARCHIVE_ZIP = "zip"
ARCHIVE_LHA_SUFFIXES = ("lha", "lzh")
ARCHIVE_SUFFIXES = (ARCHIVE_ZIP, *ARCHIVE_LHA_SUFFIXES)

KIND_FILE: PathKind = "file"
KIND_DIRECTORY: PathKind = "directory"
KIND_MISSING: PathKind = "missing"
KIND_INACCESSIBLE: PathKind = "inaccessible"

GLOB_COMPLETE: GlobStatus = "complete"
GLOB_INCOMPLETE: GlobStatus = "incomplete"


@dataclass(frozen=True, slots=True)
class ReadResult:
    """One text read's explicit outcome — ``text`` is set exactly when ``status`` is ok.

    ``missing`` means the path (or a parent component) does not exist;
    ``unreadable`` means it exists but cannot be read (permissions, a
    directory, I/O error); ``invalid-text`` means bytes exist but are not
    valid UTF-8 text. The distinctions are health signals, never collapsed.
    """

    status: ReadStatus
    text: str | None = None

    def __post_init__(self) -> None:
        if (self.text is None) == (self.status == READ_OK):
            raise ValueError(f"ReadResult: text must be set exactly when status is 'ok' (got {self.status!r})")


@dataclass(frozen=True, slots=True)
class AppImageReadResult:
    """One AppImage-entry read's explicit outcome — the plain read's shape, wider.

    The three extra statuses are documented on :data:`AppImageReadStatus`;
    ``missing`` / ``unreadable`` / ``invalid-text`` mean what they mean on
    :class:`ReadResult`, with ``invalid-text`` judging the *entry's* bytes.
    """

    status: AppImageReadStatus
    text: str | None = None

    def __post_init__(self) -> None:
        if (self.text is None) == (self.status == READ_OK):
            raise ValueError(
                f"AppImageReadResult: text must be set exactly when status is 'ok' (got {self.status!r})"
            )


@dataclass(frozen=True, slots=True)
class Ps2BiosHeaderResult:
    """One ROMDIR header read's explicit outcome — ``header`` is set exactly when ``status`` is ok.

    ``missing`` / ``unreadable`` mean what they mean on :class:`ReadResult`;
    ``not-a-bios`` is documented on :data:`Ps2BiosHeaderStatus`. The header is
    :class:`atlas.ps2_bios.Ps2BiosHeader`, the fields the core extracts.
    """

    status: Ps2BiosHeaderStatus
    header: ps2_bios.Ps2BiosHeader | None = None

    def __post_init__(self) -> None:
        if (self.header is None) == (self.status == PS2_BIOS_OK):
            raise ValueError(
                f"Ps2BiosHeaderResult: header must be set exactly when status is 'ok' (got {self.status!r})"
            )


@dataclass(frozen=True, slots=True)
class ArchiveListResult:
    """One archive listing's explicit outcome — the member names, in the archive's own order.

    ``members`` are archive-internal paths with ``/`` separators, and they are
    empty for every status but ``ok`` — where an ``ok`` listing may still be
    empty, because an archive with nothing in it is a real state and a
    different one from a file that could not be opened. The order is the
    archive's own: it is what an emulator walking the container sees.
    """

    status: ArchiveStatus
    members: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.members and self.status != ARCHIVE_OK:
            raise ValueError(
                f"ArchiveListResult: members are listed only by an 'ok' read (got {self.status!r})"
            )


@dataclass(frozen=True, slots=True)
class WhdloadSlaveResult:
    """What the slave inside an archive states — the member it came from, and its two fields.

    ``slave`` is the member the read was made of, ``version`` its
    ``ws_Version`` and ``selected_by`` how it was arrived at
    (:data:`atlas.whdload.SELECTION_ROUTES`); all three are set exactly when
    ``status`` is ok, because every other status is a read that produced no
    slave to state anything about. ``selected_by`` is what keeps the two
    routes apart: ``script`` is the core's own boot-script search, while
    ``only-slave`` is atlas's inference from an archive that offers WHDLoad
    exactly one — a derived claim, and one a caller may want to weigh
    differently.

    ``custom`` is the text of a ``custom`` file at the mounted root, or
    ``None`` where there is none. It rides every status the container itself
    was read for, because it is a fact about the container rather than about
    the slave — and it is read at all because its contents are appended to
    WHDLoad's own arguments and can move the save directory out from under
    every mode a card states.

    ``name`` is ``ws_name``, and it is the one field that can be absent from a
    successful read: a slave older than
    :data:`atlas.whdload.NAMED_FROM_VERSION` carries no such field at all, and
    the manual does not say which spelling of the file name WHDLoad falls back
    to — so the answer is *no name*, never a guessed one.
    """

    status: WhdloadSlaveStatus
    slave: str | None = None
    version: int | None = None
    name: str | None = None
    selected_by: str | None = None
    custom: str | None = None

    def __post_init__(self) -> None:
        stated = self.slave is not None and self.version is not None and self.selected_by is not None
        if stated != (self.status == WHDLOAD_OK):
            raise ValueError(
                "WhdloadSlaveResult: the member, its version and the route that named it are "
                f"stated exactly when status is 'ok' (got {self.status!r})"
            )
        if self.name is not None and self.status != WHDLOAD_OK:
            raise ValueError(
                f"WhdloadSlaveResult: only an 'ok' read names a program (got {self.status!r})"
            )


@dataclass(frozen=True, slots=True)
class GlobResult:
    """One glob's explicit outcome — what matched, and what could not be read.

    ``unreadable`` is non-empty exactly when ``status`` is ``incomplete``, the
    same shape of invariant :class:`ReadResult` carries. The difference is that
    a failed glob still has an answer worth having: a pattern spanning several
    directories can read some and not others, so ``matches`` is what was found
    *and* ``unreadable`` names where the walk stopped short. A caller that only
    wants the files can read ``matches`` and be no worse off than with a bare
    list; a caller that would otherwise say "there is nothing there" has to look
    at the status first, which is the whole point of the type.

    ``unreadable`` holds directories that could not be listed and entries that
    could not be looked at — the places, not the reasons. Whether it was
    permissions, a symlink loop, or a card that stopped answering is not a
    distinction any caller here acts on, and the path is what a message needs.
    """

    status: GlobStatus
    matches: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if bool(self.unreadable) != (self.status == GLOB_INCOMPLETE):
            raise ValueError(
                "GlobResult: unreadable must be non-empty exactly when status is 'incomplete' "
                f"(got {self.status!r} with {len(self.unreadable)} unreadable paths)"
            )


@dataclass(frozen=True, slots=True)
class CoreOption:
    """One option definition a core registers: its default and legal values.

    Captured from the registration call the core makes during
    ``retro_set_environment`` — the same declaration RetroArch's option
    manager validates persisted values against. A live read of the binary,
    never shipped data.
    """

    key: str
    default: str | None
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CoreInfo:
    """What a libretro core reports about itself.

    ``library_name`` (via ``retro_get_system_info``) is the value RetroArch
    uses for sort-by-core directories and override directories — the display
    name, not the ``.so`` basename: the two disagree for 183 of the 210 loadable
    cores RetroDECK ships (reference machine, recounted 2026-08-05). ``options``
    is the set of option definitions the core registered during
    ``retro_set_environment`` — the observable fact that identifies a
    core *generation* better than any version string. ``None`` means *not
    captured* (the probe saw no registration — some cores register later, in
    ``retro_init``): unknown, never "registers nothing".

    ``block_extract`` is the same struct's archive statement
    (``retro_system_info.block_extract``): true means RetroArch hands the
    core an archive raw instead of picking a matching file out of it
    (task_content.c:742, :1735 @ a79435a). ``None`` is a fixture or record
    that never captured it — unknown, never "false".
    """

    library_name: str
    library_version: str | None
    valid_extensions: str | None
    options: Mapping[str, CoreOption] | None = None
    block_extract: bool | None = None

    def __post_init__(self) -> None:
        if self.options is not None:
            object.__setattr__(self, "options", MappingProxyType(dict(self.options)))


_GLOB_MAGIC = frozenset("*?[")


def _has_magic(text: str) -> bool:
    return bool(_GLOB_MAGIC & set(text))


@dataclass(slots=True)
class _GlobWalk:
    """The glob algorithm itself, over three primitives each machine supplies.

    One algorithm, two data sources — the same reason the seam exists at all.
    Pattern decomposition and result *spelling* are subtle enough that two
    independent implementations drifted apart on doubled separators alone, and
    a fixture that spells a match differently from the machine makes the vector
    built on it prove nothing. So the machines supply only what they alone can
    answer, and share everything derived from it.

    The decomposition is CPython's (``glob.py``: split off the last segment,
    glob the rest, then read inside each result), because that is what decides
    the spelling: ``os.path.split`` strips the separator run before the last
    segment while runs deeper inside survive verbatim, so ``a//b//*`` answers
    ``a//b/x``. A left-to-right walk cannot reproduce that, and the real
    machine's own glob is the thing being modelled.

    Each primitive answers ``None`` for *could not tell*, which is what the
    stdlib throws away (``glob.py:173`` returns on any ``OSError``) and what
    this exists to state. A truthful negative — the name is not there, the
    component is not a directory — is ``False``/``[]``, never ``None``.
    """

    list_dir: Callable[[str], list[str] | None]
    is_dir: Callable[[str], bool | None]
    lexists: Callable[[str], bool | None]
    unreadable: set[str] = field(default_factory=set)

    def run(self, pattern: str) -> GlobResult:
        if not pattern.startswith("/"):
            # A relative pattern resolves against the process's working
            # directory, which is not a fact about the machine being read.
            return GlobResult(GLOB_COMPLETE)
        matches = self._glob(pattern, dironly=False)
        if not self.unreadable:
            return GlobResult(GLOB_COMPLETE, tuple(sorted(matches)))
        return GlobResult(GLOB_INCOMPLETE, tuple(sorted(matches)), tuple(sorted(self.unreadable)))

    def _glob(self, pathname: str, *, dironly: bool) -> list[str]:
        """Every path *pathname* matches, spelled the way the pattern reached it."""
        dirname, basename = os.path.split(pathname)
        if not _has_magic(pathname):
            return [pathname] if self._literal_is_there(pathname, dirname, basename) else []
        if _has_magic(dirname):
            parents = self._glob(dirname, dironly=True)
        else:
            # No wildcard above: the directory is taken as spelled, and whether
            # it is there at all is answered by reading inside it.
            parents = [dirname]
        return [
            os.path.join(parent, name)
            for parent in parents
            for name in self._names_in(parent, basename, dironly=dironly)
        ]

    def _literal_is_there(self, pathname: str, dirname: str, basename: str) -> bool:
        """A pattern with no wildcard at all: it matches itself, if it is there.

        A trailing separator asks about the directory instead, and follows
        links to decide — ``link-to-dir/`` is a directory, ``link-to-file/`` is
        not, and a dead one is not either.
        """
        answer = self.lexists(pathname) if basename else self.is_dir(dirname)
        if answer is None:
            self.unreadable.add(pathname if basename else dirname)
            return False
        return answer

    def _names_in(self, dirname: str, basename: str, *, dironly: bool) -> list[str]:
        """The names inside *dirname* that *basename* selects."""
        if not _has_magic(basename):
            return self._literal_name_in(dirname, basename)
        names = self.list_dir(dirname)
        if names is None:
            self.unreadable.add(dirname)
            return []
        if not basename.startswith("."):
            # A wildcard never matches a leading dot; a segment that starts
            # with one is how a pattern asks for hidden names.
            names = [name for name in names if not name.startswith(".")]
        matched = fnmatch.filter(names, basename)
        return self._directories_among(dirname, matched) if dironly else matched

    def _literal_name_in(self, dirname: str, basename: str) -> list[str]:
        """A literal segment under a globbed parent — asked about, never listed.

        Listing is not the same read: a directory may be searchable and not
        readable, and then a name atlas already knows is answerable while the
        listing is not. Asking is also what the stdlib does, so the two agree
        wherever nothing fails.
        """
        target = os.path.join(dirname, basename) if basename else dirname
        # An empty basename is a pattern's trailing separator: it asks whether
        # the directory is one, not whether a name is there.
        answer = self.lexists(target) if basename else self.is_dir(target)
        if answer is None:
            self.unreadable.add(target)
            return []
        return [basename] if answer else []

    def _directories_among(self, dirname: str, names: list[str]) -> list[str]:
        """Only the names a wildcard may descend through — directories, links followed."""
        kept: list[str] = []
        for name in names:
            joined = os.path.join(dirname, name)
            answer = self.is_dir(joined)
            if answer is None:
                self.unreadable.add(joined)
            elif answer:
                kept.append(name)
        return kept


class _ArchiveOutcome(Exception):
    """An archive read that stopped short, carrying the status to answer with.

    The two archive reads share their opening steps, and each step can end the
    read for a reason the caller states verbatim — so the reason travels as
    the status itself rather than being re-derived from an exception type at
    every return.
    """

    def __init__(self, status: ArchiveStatus) -> None:
        super().__init__(status)
        self.status: ArchiveStatus = status


# What a member decode can raise short of an outright read failure: a
# container defect, a compression method neither reader implements, a member
# the listing named and the container cannot produce, bytes that are no
# slave. Each is the same claim to a caller — the slave was selected and its
# content did not come back.
_SLAVE_UNREADABLE_ERRORS = (
    lha.LhaError,
    whdload.NotASlave,
    zipfile.BadZipFile,
    KeyError,
    NotImplementedError,
    RuntimeError,
    EOFError,
    StopIteration,
)
# A WHDLoad member inside a zip that is itself an LhA: a second container,
# extracted only while the core runs, so its bytes are nowhere atlas can read.
_NESTED_ARCHIVE_SUFFIX = ".lha"


def _archive_suffix(path: str) -> str:
    """The path's extension, lowered and without the dot — what picks the reader."""
    return os.path.splitext(path)[1].lower().lstrip(".")


def _container_suffix(container: str) -> str:
    """Which reader a mounted container takes, asking what it *is* before what it is called.

    The core tests ``path_is_directory`` before it looks at any extension
    (libretro-dc.c:850-853 before :862-864; libretro-core.c:5710 mounts on the
    same test), so a directory named ``Game.zip`` is a directory and its
    members are files under it. Every read of a container goes through this
    rather than through the extension alone, or such a directory would be
    handed to ``zipfile`` and answer unreadable for its whole contents.
    """
    return "" if os.path.isdir(container) else _archive_suffix(container)


def _stat_regular_file(path: str) -> None:
    """Refuse anything but a regular file before it is opened.

    The same check ``file_digest`` makes and for the same reason: these paths
    come out of a launch command, and opening a FIFO with no writer blocks
    forever — a hang is not a degraded answer, it is no answer at all.
    """
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        raise _ArchiveOutcome(ARCHIVE_MISSING) from None
    except OSError:
        raise _ArchiveOutcome(ARCHIVE_UNREADABLE) from None
    if not _stat.S_ISREG(st.st_mode):
        raise _ArchiveOutcome(ARCHIVE_UNREADABLE)


def _archive_bytes(path: str) -> bytes:
    """A whole LhA archive in memory — its headers are spread through the file."""
    _stat_regular_file(path)
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        raise _ArchiveOutcome(ARCHIVE_UNREADABLE) from None


def _archive_names(path: str, suffix: str) -> tuple[str, ...]:
    """The member names one archive holds, as the tree it extracts to spells them."""
    return tuple(_normalise(raw) for raw in _raw_names(path, suffix) if _normalise(raw))


def _raw_names(path: str, suffix: str) -> tuple[str, ...]:
    """The member names the archive itself carries, by the reader its suffix picks."""
    if suffix == ARCHIVE_ZIP:
        return _zip_names(path)
    try:
        return tuple(member.name for member in lha.members(_archive_bytes(path)))
    except lha.NotAnLha:
        raise _ArchiveOutcome(ARCHIVE_NOT_ARCHIVE) from None


def _normalise(member: str) -> str:
    """One member name as the extracted tree spells it: relative, no ``./`` in front.

    An archive may write a member as ``./Disk1.adf`` or with a leading ``/``,
    and what lands on disk is ``Disk1.adf`` either way. Leaving the spelling
    alone would hide such a member from a walk that passes over names starting
    with a dot, which is exactly the walk the core makes — so the listing is
    normalised, and :func:`_original_name` maps back when bytes are wanted.
    """
    name = member.lstrip("/")
    while name.startswith("./"):
        name = name[2:]
    return name


def _original_name(container: str, member: str) -> str:
    """The spelling the container itself uses for a member the listing normalised.

    A directory's listing is already its own spelling; an archive's may not
    be, and opening ``Game.slave`` in an archive that wrote ``./Game.slave``
    finds nothing.
    """
    suffix = _container_suffix(container)
    if suffix not in ARCHIVE_SUFFIXES:
        return member
    return next((raw for raw in _raw_names(container, suffix) if _normalise(raw) == member), member)


def _zip_names(path: str) -> tuple[str, ...]:
    _stat_regular_file(path)
    try:
        with zipfile.ZipFile(path) as archive:
            return tuple(archive.namelist())
    except zipfile.BadZipFile:
        raise _ArchiveOutcome(ARCHIVE_NOT_ARCHIVE) from None
    except OSError:
        raise _ArchiveOutcome(ARCHIVE_UNREADABLE) from None


def _directory_names(path: str) -> tuple[str, ...]:
    """Everything under one mounted directory, relative and ``/``-separated.

    Links are not followed: a mounted volume's tree is what lies in it, and
    following one is how a walk finds a cycle. A subdirectory that cannot be
    read makes the whole listing unreadable rather than short — the slave
    search decides on what is *not* there as much as on what is, so a partial
    listing would answer a question about a volume nobody described.
    """
    found: list[str] = []
    failed: list[OSError] = []
    for base, _, files in os.walk(path, onerror=failed.append):
        for name in files:
            found.append(os.path.relpath(os.path.join(base, name), path).replace(os.sep, "/"))
    if failed:
        raise _ArchiveOutcome(ARCHIVE_UNREADABLE)
    return tuple(found)


def _mounted_root(container: str, members: tuple[str, ...]) -> str | None:
    """The prefix inside the container the core leaves mounted, or ``None`` where its order decides.

    Only an extracted archive has such a prefix: an ``.lha`` and a directory
    are mounted whole. A WHDLoad member that is itself an ``.lha`` is a second
    container, extracted only while the core runs, so nothing on disk holds
    its slave for this seam to read.
    """
    if _container_suffix(container) != ARCHIVE_ZIP:
        return ""
    accepted = whdload.accepted_members(members)
    if any(name.lower().endswith(_NESTED_ARCHIVE_SUFFIX) for name in accepted):
        return None
    return whdload.extracted_root(members)


def _no_root_status(members: tuple[str, ...]) -> WhdloadSlaveStatus:
    """Why no root was resolved: a second container, or an order that decides.

    They are different claims. A WHDLoad member that is itself an ``.lha``
    holds its slave inside a container extracted only while the core runs, so
    nothing here can name one at all; members resolving to different drawers
    are two mounts the core's own listing order chooses between.
    """
    nested = any(
        name.lower().endswith(_NESTED_ARCHIVE_SUFFIX) for name in whdload.accepted_members(members)
    )
    return WHDLOAD_NO_SLAVE if nested else WHDLOAD_AMBIGUOUS


def _slave_of(
    container: str, root: str, inside: list[str], selection: whdload.Selection
) -> WhdloadSlaveResult:
    """One selection turned into the seam's answer, with the ``custom`` file beside it.

    A ``load`` file at the root replaces the launch with the volume's own
    command, and the block that would have read ``custom`` is inside the
    branch it skips (Startup-Sequence:61-62 over :115-122) — so where one is
    there, no ``custom`` is read either.
    """
    overridden = any(name.lower() == whdload.LOAD for name in inside)
    custom = None if overridden else _custom_text(container, root, inside)
    if selection.slave is None:
        status = WHDLOAD_AMBIGUOUS if selection.ambiguous else WHDLOAD_NO_SLAVE
        return WhdloadSlaveResult(status, custom=custom)
    try:
        member = _original_name(container, root + selection.slave)
        slave = whdload.read_slave(_member_bytes(container, member))
    except _ArchiveOutcome as outcome:
        return WhdloadSlaveResult(outcome.status)
    except _SLAVE_UNREADABLE_ERRORS:
        return WhdloadSlaveResult(WHDLOAD_SLAVE_UNREADABLE, custom=custom)
    return WhdloadSlaveResult(
        WHDLOAD_OK, root + selection.slave, slave.version, slave.name, selection.route, custom
    )


def _custom_text(container: str, root: str, inside: list[str]) -> str | None:
    """The ``custom`` file at the mounted root, as text — ``None`` where there is none.

    It is read rather than merely noticed because what it says decides where
    the saves go: its contents are appended to WHDLoad's own arguments and
    "always override WHDLoad.prefs" (README.md:364). Latin-1, one byte per
    character, which is what an Amiga wrote.
    """
    named = next((name for name in inside if name.lower() == whdload.CUSTOM), None)
    if named is None:
        return None
    try:
        return _member_bytes(container, _original_name(container, root + named)).decode("latin-1")
    except (_ArchiveOutcome, *_SLAVE_UNREADABLE_ERRORS):
        return None


def _member_bytes(container: str, member: str) -> bytes:
    """One member's bytes, out of whichever container this is."""
    suffix = _container_suffix(container)
    if suffix == ARCHIVE_ZIP:
        _stat_regular_file(container)
        with zipfile.ZipFile(container) as archive:
            return archive.read(member)
    if suffix in ARCHIVE_LHA_SUFFIXES:
        data = _archive_bytes(container)
        entry = next(found for found in lha.members(data) if found.name == member)
        return lha.extract(data, entry)
    return _plain_bytes(os.path.join(container, member))


def _plain_bytes(path: str) -> bytes:
    _stat_regular_file(path)
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        raise _ArchiveOutcome(ARCHIVE_UNREADABLE) from None


class Machine(Protocol):
    """Narrow machine port: read a file, glob, classify a path, follow links, ask a core.

    Every operation reports an explicit outcome; the caller decides what a
    failure means — the seam never guesses. ``glob`` follows the normative
    semantics in the module docstring and says how much of its walk it could
    read; a caller that would otherwise report "nothing is there" must look at
    that before believing an empty ``matches``. ``readlink`` returns the link target
    when the path itself is a symlink, else ``None``. ``query_core`` returns
    the core's self-reported info, or ``None`` whenever that info cannot be
    had — the core would not load, or nothing here could load it in the first
    place; the caller treats either as *unknown*, never as a guess.
    ``file_size`` and ``file_digest`` answer for regular files only and return
    ``None`` whenever the answer cannot be determined (missing, unreadable, not
    a regular file, or an algorithm outside :data:`DIGEST_ALGORITHMS`).
    """

    def read_text(self, path: str) -> ReadResult: ...

    def read_appimage_text(self, path: str, inner_path: str) -> AppImageReadResult: ...

    def read_ps2_bios_header(self, path: str) -> Ps2BiosHeaderResult: ...

    def list_archive(self, path: str) -> ArchiveListResult: ...

    def read_whdload_slave(self, path: str) -> WhdloadSlaveResult: ...

    def glob(self, pattern: str) -> GlobResult: ...

    def path_kind(self, path: str) -> PathKind: ...

    def readlink(self, path: str) -> str | None: ...

    def query_core(self, so_path: str) -> CoreInfo | None: ...

    def file_size(self, path: str) -> int | None: ...

    def file_digest(self, path: str, algorithm: str) -> str | None: ...


class RealMachine:
    """The production machine: the real filesystem, plus a core prober where one is possible.

    ``query_core`` runs the probe in a subprocess (``atlas._core_probe``) — so
    a crashing core costs one answer, not the host process — wherever an
    interpreter to run it under could be named
    (:func:`core_probe_interpreter`), and memoizes per ``(path, mtime, size)``:
    a cached live read, not shipped data, keyed on the file's metadata rather
    than its content — a rebuild moves the mtime and is read again, while a
    replacement preserving mtime and size (``cp -p``, a timestamp-normalising
    deploy) keeps the key. A probe that timed out without printing a usable
    line is remembered too, so a hanging core costs its timeout once per
    machine rather than once per question. That memory reaches exactly as far
    as this object and no further, which is the part a consumer has to act on:
    :func:`atlas.detect` builds a fresh machine whenever it is handed none, so
    a caller that re-detects per question pays the timeout every time, while
    one that keeps its installations, or passes its own machine, pays it once.
    Where no interpreter can be named nothing is launched at all and every core
    answers *unknown*. The child is pointed back at this package
    (:func:`_probe_environment`) and answers with whatever it printed before it
    stopped (:func:`_parse_probe_output`).
    """

    def __init__(self) -> None:
        self._core_cache: dict[tuple[str, int, int], CoreInfo | None] = {}

    def read_text(self, path: str) -> ReadResult:
        # Regular files only, checked BEFORE opening — for the same reason
        # file_digest checks: opening a FIFO with no writer blocks forever, and
        # these paths come out of config files. A ``.info`` that is a FIFO would
        # hang the whole firmware answer rather than degrade it.
        try:
            st = os.stat(path)
        except (FileNotFoundError, NotADirectoryError):
            return ReadResult(READ_MISSING)
        except OSError:
            return ReadResult(READ_UNREADABLE)
        if not _stat.S_ISREG(st.st_mode):
            # A directory, a FIFO, a device: present, and not readable as text.
            return ReadResult(READ_UNREADABLE)
        try:
            with open(path, encoding="utf-8") as f:
                return ReadResult(READ_OK, f.read())
        except (FileNotFoundError, NotADirectoryError):
            return ReadResult(READ_MISSING)
        except UnicodeDecodeError:
            return ReadResult(READ_INVALID_TEXT)
        except OSError:
            # Permissions, I/O failure: present but unreadable.
            return ReadResult(READ_UNREADABLE)

    def read_appimage_text(self, path: str, inner_path: str) -> AppImageReadResult:
        """One entry out of an AppImage's embedded squashfs, decoded as UTF-8.

        The reader is :mod:`atlas.squashfs`; every failure it can name maps to
        its own status, because the caller's next step differs for each — a
        missing file is the AppImage being gone, ``capability-missing`` is
        this interpreter lacking the image's codec while the file is fine.
        """
        try:
            data = squashfs.read_appimage_entry(path, inner_path)
        except (FileNotFoundError, NotADirectoryError):
            return AppImageReadResult(READ_MISSING)
        except IsADirectoryError:
            return AppImageReadResult(READ_UNREADABLE)
        except squashfs.CodecUnavailable:
            return AppImageReadResult(APPIMAGE_CAPABILITY_MISSING)
        except squashfs.EntryNotFound:
            return AppImageReadResult(APPIMAGE_ENTRY_MISSING)
        except squashfs.SquashfsError:
            return AppImageReadResult(APPIMAGE_NOT_APPIMAGE)
        except OSError:
            return AppImageReadResult(READ_UNREADABLE)
        try:
            return AppImageReadResult(READ_OK, data.decode("utf-8"))
        except UnicodeDecodeError:
            return AppImageReadResult(READ_INVALID_TEXT)

    def read_ps2_bios_header(self, path: str) -> Ps2BiosHeaderResult:
        """The ROMDIR read LRPS2 makes over a folder candidate — a status, and the fields where it passes.

        Regular files only, checked before opening, for the reason
        ``file_digest`` checks: the folder route hands this whatever a listed
        directory holds. The reader is :mod:`atlas.ps2_bios`; its one refusal
        maps to ``not-a-bios``, and every read failure keeps the plain read's
        words, so a caller can tell a file that failed the test from a file
        that could not be given it.
        """
        try:
            st = os.stat(path)
        except (FileNotFoundError, NotADirectoryError):
            return Ps2BiosHeaderResult(PS2_BIOS_MISSING)
        except OSError:
            return Ps2BiosHeaderResult(PS2_BIOS_UNREADABLE)
        if not _stat.S_ISREG(st.st_mode):
            return Ps2BiosHeaderResult(PS2_BIOS_UNREADABLE)
        try:
            with open(path, "rb") as f:
                header = ps2_bios.read_header(f)
        except (FileNotFoundError, NotADirectoryError):
            return Ps2BiosHeaderResult(PS2_BIOS_MISSING)
        except ps2_bios.NotAPs2Bios:
            return Ps2BiosHeaderResult(PS2_BIOS_NOT_A_BIOS)
        except OSError:
            return Ps2BiosHeaderResult(PS2_BIOS_UNREADABLE)
        return Ps2BiosHeaderResult(PS2_BIOS_OK, header)

    def list_archive(self, path: str) -> ArchiveListResult:
        """What one container holds — the read PUAE's own walk over a mounted volume makes.

        One rule decides which container this is, and both machines and the
        vector validator share it, in the order the core's own dispatch makes
        the tests: a **directory** is listed as itself whatever it is called
        (the core mounts one as a filesystem exactly as it mounts an archive),
        then an archive suffix is read by the archive reader, and anything
        else is not a container this reads. Regular files only for the archive half,
        checked before opening, for the reason :meth:`read_text` checks.
        """
        kind = self.path_kind(path)
        if kind == KIND_MISSING:
            return ArchiveListResult(ARCHIVE_MISSING)
        try:
            # A directory first, the way the core's own dispatch tests it: a
            # directory named Game.zip is a directory to it, not an archive
            # (libretro-dc.c:850-853 before :862-864 at 0043cf9).
            if kind == KIND_DIRECTORY:
                return ArchiveListResult(ARCHIVE_OK, _directory_names(path))
            suffix = _archive_suffix(path)
            if suffix in ARCHIVE_SUFFIXES:
                return ArchiveListResult(ARCHIVE_OK, _archive_names(path, suffix))
        except _ArchiveOutcome as outcome:
            return ArchiveListResult(outcome.status)
        return ArchiveListResult(ARCHIVE_NOT_ARCHIVE)

    def read_whdload_slave(self, path: str) -> WhdloadSlaveResult:
        """The slave the core would launch for this content, and what it states.

        The mount comes first: an archive and a directory are mounted as
        themselves, a ``.slave`` or an ``.info`` mounts the drawer beside it
        (:func:`atlas.whdload.mounted_container`). That container is listed,
        an extracted archive is narrowed to the drawer its own walk would
        leave mounted, the boot script's search picks the member out of what
        is left, and only that one member is read. A zip whose WHDLoad member
        is itself an ``.lha`` is a second container in the way, which this
        seam does not open.
        """
        container = whdload.mounted_container(path, self._is_directory)
        listed = self.list_archive(container)
        if listed.status != ARCHIVE_OK:
            return WhdloadSlaveResult(listed.status)
        root = _mounted_root(container, listed.members)
        if root is None:
            return WhdloadSlaveResult(_no_root_status(listed.members))
        inside = [name[len(root) :] for name in listed.members if name.startswith(root)]
        return _slave_of(container, root, inside, whdload.select_slave(inside))

    def _is_directory(self, path: str) -> bool:
        return self.path_kind(path) == KIND_DIRECTORY

    def glob(self, pattern: str) -> GlobResult:
        return _GlobWalk(self._list_dir, self._is_dir, self._lexists).run(pattern)

    @staticmethod
    def _list_dir(path: str) -> list[str] | None:
        """The names in one directory, or ``None`` when it could not be read.

        The split is the seam's usual one: a path that is not there and a
        component that is not a directory are truthful negatives, everything
        else is a read that failed. ``os.scandir`` was observed to raise
        ``PermissionError`` on a mode-000 directory and ``OSError(ELOOP)`` on a
        link cycle — the two states the stdlib's glob turns into an empty
        listing indistinguishable from an empty directory.
        """
        try:
            with os.scandir(path) as entries:
                return [entry.name for entry in entries]
        except (FileNotFoundError, NotADirectoryError):
            return []
        except OSError:
            return None

    @staticmethod
    def _is_dir(path: str) -> bool | None:
        # Follows links, like the stdlib's per-entry ``is_dir()``: a link to a
        # directory may be descended through, a dead one may not, and a cycle
        # cannot be decided at all.
        try:
            return _stat.S_ISDIR(os.stat(path).st_mode)
        except (FileNotFoundError, NotADirectoryError):
            return False
        except OSError:
            return None

    @staticmethod
    def _lexists(path: str) -> bool | None:
        # lstat, not stat: a dead symlink is a name a listing shows, so a glob
        # matches it (and the resolver has to see it — RetroDECK's dir_prep
        # leaves them behind).
        try:
            os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            return False
        except OSError:
            return None
        return True

    def path_kind(self, path: str) -> PathKind:
        try:
            st = os.stat(path)
        except (FileNotFoundError, NotADirectoryError):
            return KIND_MISSING
        except OSError:
            return KIND_INACCESSIBLE
        return KIND_DIRECTORY if _stat.S_ISDIR(st.st_mode) else KIND_FILE

    def readlink(self, path: str) -> str | None:
        try:
            return os.readlink(path) if os.path.islink(path) else None
        except OSError:
            return None

    def file_size(self, path: str) -> int | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return st.st_size if _stat.S_ISREG(st.st_mode) else None

    def file_digest(self, path: str, algorithm: str) -> str | None:
        if algorithm not in DIGEST_ALGORITHMS:
            return None
        # Regular files only, checked BEFORE opening: reading a FIFO or a
        # character device blocks forever, and this runs inside a library entry
        # point that hashes whatever a config points at. A hang is not a
        # degraded answer, it is no answer at all.
        try:
            st = os.stat(path)
        except OSError:
            return None
        if not _stat.S_ISREG(st.st_mode):
            return None
        digest = hashlib.new(algorithm)
        try:
            with open(path, "rb") as f:
                while chunk := f.read(_DIGEST_CHUNK_BYTES):
                    digest.update(chunk)
        except OSError:
            # Unreadable, or an I/O failure mid-read: the identity cannot be
            # stated. (A path that stopped being a regular file between the
            # stat and the open lands here too.)
            return None
        return digest.hexdigest()

    def query_core(self, so_path: str) -> CoreInfo | None:
        try:
            st = os.stat(so_path)
        except OSError:
            return None
        key = (so_path, st.st_mtime_ns, st.st_size)
        if key in self._core_cache:
            return self._core_cache[key]
        info, timed_out = self._probe(so_path)
        # An answer is memoized, and so is the absence of one after a timeout —
        # whatever the hung core managed to print, no usable line came out of
        # it, or info would be that answer. A core that hung once hangs again,
        # and that retry is the only empty answer costing the caller the whole
        # _CORE_PROBE_TIMEOUT_SECONDS. Every other empty answer is asked again,
        # because it can be transient (missing host library installed later)
        # even while the .so is unchanged. The memory is this object's and goes
        # no further: a caller that builds a machine per question re-probes.
        if info is not None or timed_out:
            self._core_cache[key] = info
        return info

    @staticmethod
    def _probe(so_path: str) -> _ProbeResult:
        interpreter = core_probe_interpreter()
        if interpreter is None:
            # No interpreter to run the probe under, so nothing is launched at
            # all. The alternative — spawning ``sys.executable`` and hoping —
            # starts the *host* again wherever that executable is a frozen
            # application, and ``capture_output`` would swallow the evidence.
            # Unknown is the honest answer, and the caller already handles it.
            # Nothing ran, so nothing timed out: the next question asks again,
            # and it is free — an interpreter may be registered by then.
            return _ProbeResult(None, timed_out=False)
        try:
            proc = subprocess.run(
                [interpreter.path, "-m", "atlas._core_probe", so_path],
                capture_output=True,
                timeout=_CORE_PROBE_TIMEOUT_SECONDS,
                env=_probe_environment(),
            )
        except subprocess.TimeoutExpired as expired:
            # A core that hangs in the option-capture phase printed its base
            # answer before it hung; the exception carries what was captured.
            return _ProbeResult(_parse_probe_output(expired.stdout), timed_out=True)
        except OSError:
            # The probe never ran — nothing was read, nothing can be said.
            return _ProbeResult(None, timed_out=False)
        return _ProbeResult(_parse_probe_output(proc.stdout), timed_out=False)


class _ProbeResult(NamedTuple):
    """What one probe read, and whether the process had to be killed to end it.

    Two facts, because ``query_core`` decides on both. What was read is the
    answer; how the run ended is what tells an empty answer that will stay
    empty from one that may not. A probe that timed out and printed no usable
    line would hang the same way next time, so ``query_core`` remembers that
    nothing and pays ``_CORE_PROBE_TIMEOUT_SECONDS`` once per ``.so`` for the
    life of that :class:`RealMachine`; every other empty answer is asked again,
    because the host library that was missing can be installed while the
    ``.so`` never changes.

    The ending is carried here rather than folded into the answer because
    :func:`_parse_probe_output` reads bytes and nothing else — a timeout that
    printed a usable line still answers with it, and is remembered as the
    success it is.
    """

    info: CoreInfo | None
    timed_out: bool


def _parse_probe_output(stdout: bytes | None) -> CoreInfo | None:
    """Read the core's answer out of whatever the probe printed before it stopped.

    The probe prints one JSON object per line and later lines enrich earlier
    ones (two-phase design), so the last valid line wins. How the process
    *ended* is deliberately not consulted: the subprocess exists because cores
    crash — in ``retro_set_environment``, the option-capture phase — and by
    then the phase-1 answer carrying ``library_name`` has already been
    delivered. Discarding it on a non-zero exit would throw away a read that
    succeeded. A run that printed no usable line is ``None``: unknown, whether
    it exited cleanly or not.
    """
    data: dict[str, object] | None = None
    for line in (stdout or b"").decode("utf-8", "replace").splitlines():
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict):
            data = candidate
    if data is None:
        return None
    name = data.get("library_name")
    if not isinstance(name, str) or not name:
        return None
    version = data.get("library_version")
    extensions = data.get("valid_extensions")
    block_extract = data.get("block_extract")
    return CoreInfo(
        library_name=name,
        library_version=version if isinstance(version, str) else None,
        valid_extensions=extensions if isinstance(extensions, str) else None,
        options=_parse_core_options(data.get("options")),
        block_extract=block_extract if isinstance(block_extract, bool) else None,
    )


def _probe_environment() -> dict[str, str] | None:
    """The probe child's environment: this package's location ahead of ``PYTHONPATH``.

    The child imports ``atlas._core_probe`` by name, and under the vendoring
    model — this package copied into a host that puts it on ``sys.path`` at
    runtime — nothing on the child's default path leads back to it: every core
    would come back unknown, for a missing module rather than for anything
    about the core. Prepending the directory that holds this package keeps the
    child on the same atlas the parent runs, and leaves the rest of the
    environment, an inherited ``PYTHONPATH`` included, intact.

    ``None`` when this module has no file behind it (a frozen build): the child
    then inherits the environment unchanged, which is the best that can be said.
    """
    location = globals().get("__file__")
    if not isinstance(location, str) or not location:
        return None
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(location)))
    env = dict(os.environ)
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{package_root}{os.pathsep}{inherited}" if inherited else package_root
    return env


# The interpreter a host handed over, or None. Process-global because the
# question is the process's: one running program, one answer to "is there an
# interpreter here the core probe could run under".
_registered_interpreter: str | None = None


def _is_spawnable_interpreter_path(handed_over: object) -> bool:
    """The one shape both stages accept: an absolute ``str`` the OS can be handed.

    One rule, applied to the path a host registered and to the one derived from
    the running program alike, so neither stage can put into the spawn what the
    other would have refused. Each requirement is about the spawn:

    - a ``str``, because that is what this seam stores and reports back:
      ``CoreProbeInterpreter.path`` is a ``str``. A ``Path`` is refused for
      that reason and no other — it passes :func:`os.path.isabs`, and
      ``subprocess`` would run it perfectly well;
    - **absolute**, because ``subprocess`` resolves a bare name through
      ``PATH`` and a relative one against the process's working directory.
      Either is a lookup atlas performs nowhere, and a host reaching this seam
      may be a service running as root. The empty string is not absolute, so
      it is refused here rather than by a check of its own;
    - **something the operating system can actually be handed.** ``_probe``
      degrades an ``OSError`` to *unknown*, so a path the spawn refuses with a
      ``ValueError`` instead would escape ``query_core`` into the resolver.
      The requirement is stated that way round rather than as a list of bad
      spellings, because the list is not closed — but it takes two checks,
      because the two known spellings fail at different layers: a lone
      surrogate fails :func:`os.fsencode`, the same encoding ``subprocess``
      performs, while a NUL byte encodes cleanly and ``subprocess`` rejects it
      itself. A surrogate-escaped byte (``\\udcff``) is neither of those: it is
      what a real filesystem hands back for a name that is not valid text, it
      encodes, and it degrades like any other path that does not run.

    Typed ``object`` on purpose: the annotation on the registration below is a
    promise the caller makes, and this check is there for the caller who does
    not keep it.
    """
    if not isinstance(handed_over, str) or not os.path.isabs(handed_over):
        return False
    if "\x00" in handed_over:
        return False
    try:
        os.fsencode(handed_over)
    except ValueError:
        return False
    return True


def register_core_probe_interpreter(path: str | None) -> None:
    """Name the Python interpreter the core probe runs under — or ``None`` to forget it.

    ``query_core`` answers what only the core binary can answer, by loading it
    in a child process; that child is a Python interpreter running
    ``atlas._core_probe``. A frozen host (PyInstaller, cx_Freeze, py2exe) has
    no interpreter to offer as ``sys.executable`` — there that path is the
    *application*, whose bootloader ignores ``-m atlas._core_probe`` and starts
    the application a second time — so a host that knows where a real
    interpreter lives says so here, and atlas launches that one instead. The
    environment the child receives points it back at this package (see
    :func:`_probe_environment`), so a foreign interpreter is not a poorer
    answer: it is the same answer.

    The path must be absolute, and it must be one the operating system can
    actually be handed — :func:`_is_spawnable_interpreter_path` carries the
    reasoning for every requirement, and the derived stage applies the same
    rule. Anything that is not ``None`` and does not pass it is refused with
    :class:`TypeError` here at the registration, where the caller can still see
    what it handed over.

    Whether the file exists is deliberately **not** checked: that is the
    machine's business at probe time, and a path that does not run yields the
    same honest *unknown* every other probe failure yields. That promise is
    what the shape rule protects: a path the operating system cannot be handed
    at all makes the spawn raise a ``ValueError``, which would escape rather
    than degrade. Such a path is named here, where the caller can still see it,
    instead of turning into a statement about the machine.

    Registering ``None`` clears the registration and the running program
    decides again. The last registration wins; there is one slot, not a chain.
    """
    global _registered_interpreter
    if path is not None and not _is_spawnable_interpreter_path(path):
        raise TypeError(
            "a core probe interpreter must be an absolute path, spelled as a str the "
            f"operating system can be handed; {path!r} is not"
        )
    _registered_interpreter = path


class CoreProbeInterpreter(NamedTuple):
    """Which interpreter a core probe runs under here, and how it got here.

    ``registered`` is the route, not a guess from the path: a host may hand
    over the very interpreter that is running atlas, and then ``path`` equals
    ``sys.executable`` while the answer still came from the host.
    """

    path: str
    registered: bool


def _running_python_interpreter() -> str | None:
    """``sys.executable``, but only where the running program is plainly an interpreter.

    Launching ``sys.executable -m atlas._core_probe`` is a probe only where
    that executable *is* an interpreter. In a frozen build it is the
    application: the bootloader ignores the module arguments and starts the
    application again, so asking atlas where a save lives would restart the
    host that asked — and ``capture_output`` would hide it.

    ``sys.executable`` also has to survive the same shape check the registered
    path does (:func:`_is_spawnable_interpreter_path`), and that is not
    theoretical: ``PYTHONEXECUTABLE=python3`` makes it a bare name, which the
    spawn would resolve through ``PATH``, and ``PYTHONEXECUTABLE=dir/python3``
    makes it relative, which the spawn would resolve against the working
    directory. Refusing a lookup in one stage and performing it in the other
    would be the same defect wearing a different hat, so both stages apply the
    one rule. Do not take the check out of either of them.

    So the test narrows on purpose. The two markers freezers set
    (``sys.frozen``, ``sys._MEIPASS``) disqualify; a ``sys.executable`` the
    shape rule refuses disqualifies; and the basename is the belt for an
    embedded host that sets neither marker. It is a rule of thumb, and
    the two directions it can be wrong in are not symmetrical: every way it is
    too narrow costs a probe and nothing else — a PyPy or otherwise-named
    interpreter loses probing here and hands over its own path through
    :func:`register_core_probe_interpreter` — while the name check is what
    keeps the too-wide direction rare, since a host that embeds an interpreter,
    sets neither marker and is itself named ``python…`` would pass and be
    launched. Do not widen this into a search — a ``PATH``-resolved ``python3``
    is an assumption about the machine, and atlas makes none.
    """
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        return None
    executable = sys.executable
    if not _is_spawnable_interpreter_path(executable):
        return None
    if not os.path.basename(executable).startswith("python"):
        return None
    return executable


def core_probe_interpreter() -> CoreProbeInterpreter | None:
    """Which interpreter a core probe would run under here — ``None`` where none would.

    ``None`` is the state in which ``query_core`` starts no process at all and
    answers *unknown* for every core, which the resolver reports as
    ``core-unqueryable``; this function is the diagnosis channel for a host
    that sees that code everywhere. It says what a probe would run, not that
    any core was probed.

    The slot is read once into a local. Read twice, a registration cleared
    between the two reads would build an answer whose ``path`` is missing while
    ``registered`` still says ``True``.
    """
    registered = _registered_interpreter
    if registered is not None:
        return CoreProbeInterpreter(registered, True)
    running = _running_python_interpreter()
    return None if running is None else CoreProbeInterpreter(running, False)


def _parse_core_options(raw: object) -> dict[str, CoreOption] | None:
    """Parse a probe's / fixture's option map — ``None`` (not captured) stays ``None``.

    A malformed entry is dropped rather than invented; a wholly malformed map
    counts as not captured.
    """
    if not isinstance(raw, dict):
        return None
    options: dict[str, CoreOption] = {}
    for key, spec in raw.items():
        if not isinstance(key, str) or not key or not isinstance(spec, dict):
            continue
        default = spec.get("default")
        values = spec.get("values")
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            values = []
        options[key] = CoreOption(
            key=key,
            default=default if isinstance(default, str) else None,
            values=tuple(values),
        )
    return options


# Fixture file specs: a plain string is readable content; an object states a
# non-ok read outcome ({"status": "unreadable"} or {"status": "invalid-text"})
# or a binary blob's identity ({"md5": ..., "sha1": ..., "size": ...}).
#
# A blob is how a fixture states a firmware file: firmware is not text, and its
# identity is exactly the size and digests a real one would answer with — so
# the fixture declares them rather than carrying bytes that would have to hash
# to a real dump's md5. A string-content file needs no declaration: its size
# and digests are computed from the content, so fixture and real machine agree
# by construction.
#
# The two are independent axes because the machine answers them from two
# different reads: ``size`` comes from the ``stat``, the digests from the bytes.
# A chmod-000 file was observed to answer file (``stat`` succeeded), unreadable,
# its real size, and no digest — so ``{"status": "unreadable", "size": N}`` is a
# state a fixture must be able to spell, and the size-less spelling keeps its
# own meaning: present, unreadable, and no size either, which is what a FIFO or
# a device node answers (``file_size`` holds for regular files only).
FixtureFileSpec = str | Mapping[str, str | int]

_BLOB_KEYS = ("size", *DIGEST_ALGORITHMS)


def _file_identity(path: str, spec: Mapping[str, str | int]) -> dict[str, str | int]:
    """The identity fields of one object spec — size from the stat, digests from the bytes.

    A digest alongside ``unreadable`` is refused rather than ignored: the bytes
    are precisely what cannot be read there, a real one answers ``None`` for
    ``file_digest``, and a fixture stating one would make a vector assert a
    ``checked`` verdict the machine it models never reaches.
    """
    identity = {key: spec[key] for key in _BLOB_KEYS if key in spec}
    if spec.get("status") == READ_UNREADABLE and any(key in identity for key in DIGEST_ALGORITHMS):
        raise ValueError(
            f"fixture file {path!r}: an unreadable file states no digest — its bytes are what cannot "
            "be read, so a real one answers None; only 'size' survives, because it comes from the stat"
        )
    return identity


def _index_fixture_files(
    files: Mapping[str, FixtureFileSpec],
) -> tuple[dict[str, tuple[ReadStatus, str | None]], dict[str, dict[str, str | int]]]:
    """Split the declared file specs into read outcomes and declared identities.

    A malformed spec raises rather than being coerced: a fixture that cannot be
    read as written would otherwise prove something about a machine nobody
    described.
    """
    read: dict[str, tuple[ReadStatus, str | None]] = {}
    blobs: dict[str, dict[str, str | int]] = {}
    for path, spec in files.items():
        if isinstance(spec, str):
            read[path] = (READ_OK, spec)
            continue
        status = spec.get("status")
        identity = _file_identity(path, spec)
        if status is None:
            if not identity:
                raise ValueError(
                    f"fixture file {path!r}: an object spec must carry a 'status' or at least one "
                    f"of {list(_BLOB_KEYS)}"
                )
            # A blob exists and is not text — the same answer a real
            # firmware file gives read_text.
            read[path] = (READ_INVALID_TEXT, None)
        elif status in (READ_UNREADABLE, READ_INVALID_TEXT):
            read[path] = (status, None)
        else:
            raise ValueError(f"fixture file {path!r}: status must be 'unreadable' or 'invalid-text'")
        if identity:
            blobs[path] = identity
    return read, blobs


@dataclass(frozen=True, slots=True)
class _Landing:
    """Where a spelled path lands, or which refusal the kernel answers instead.

    ``path`` is set exactly when the walk completed. The two refusals are held
    apart because the seam answers them differently: a chain that never settles
    is ``ELOOP``, a *failing stat*, which every operation reports as
    inaccessible; a component the walk must step through that is not a
    directory is ``ENOTDIR``, which ``os.stat`` raises as ``NotADirectoryError``
    and :class:`RealMachine` reports as missing (observed: a regular file
    spelled with a trailing slash answers missing, not inaccessible).
    """

    path: str | None = None
    loops: bool = False


_LOOPS = _Landing(loops=True)
_NOT_A_DIRECTORY = _Landing()


def _stepped_onto_link(resolved: str, target: str, parts: list[str]) -> tuple[str, list[str]]:
    """Where the walk stands after following a link, and what is left to walk.

    An absolute target restarts the walk at the root; a relative one is
    relative to the directory holding the link, which is exactly where the walk
    already stands. Either way the target's own components are walked before
    whatever the spelling still had left.
    """
    rest = [p for p in target.split("/") if p and p != "."] + parts
    return ("/" if target.startswith("/") else resolved), rest


def _refuse_both_unreadable_lists(inaccessible: set[str], unlistable: set[str]) -> None:
    """A path cannot both fail its ``stat`` and be a directory whose ``stat`` succeeded.

    Refused rather than resolved, for the same reason a digest alongside
    ``unreadable`` is: a precedence rule here would be a documented silent
    degradation, and this contradiction has a tempting wrong reading — that
    both lists together spell a mode-000 directory. They do not. Such a
    directory answers *directory* about itself, so it belongs in ``unlistable``
    alone, and any precedence would quietly hand back the machine the fixture
    was not describing.
    """
    both = sorted(inaccessible & unlistable)
    if both:
        raise ValueError(
            f"fixture paths {both} are in both 'inaccessible' and 'unlistable' — a path whose stat "
            "fails cannot also be a directory whose stat succeeds. A mode-000 directory is "
            "'unlistable'; name its children in 'inaccessible' if they matter."
        )


def _ancestor_dirs(paths: Iterable[str]) -> set[str]:
    """Every ancestor of the given paths — a known path's parents are directories."""
    dirs: set[str] = set()
    for path in paths:
        parent = os.path.dirname(path)
        while parent and parent != "/":
            dirs.add(parent)
            parent = os.path.dirname(parent)
    return dirs


# The whole-archive states a fixture AppImage may declare, and the per-entry
# ones: what RealMachine can report short of an entry's text. "missing" is not
# among them — an absent AppImage is modeled by not declaring the path at all,
# and an absent entry by not declaring the entry.
_FIXTURE_APPIMAGE_STATES = ("unreadable", "not-appimage", "capability-missing")
_FIXTURE_APPIMAGE_ENTRY_STATES = ("unreadable", "invalid-text")


def _validate_fixture_appimages(
    appimages: Mapping[str, Mapping[str, object] | str],
) -> dict[str, dict[str, object] | str]:
    validated: dict[str, dict[str, object] | str] = {}
    for path, spec in appimages.items():
        if isinstance(spec, str):
            if spec not in _FIXTURE_APPIMAGE_STATES:
                raise ValueError(
                    f"appimage {path!r}: a whole-archive state must be one of "
                    f"{_FIXTURE_APPIMAGE_STATES}, got {spec!r}"
                )
            validated[path] = spec
            continue
        entries: dict[str, object] = {}
        for inner, value in spec.items():
            if isinstance(value, str):
                entries[inner] = value
                continue
            if (
                isinstance(value, Mapping)
                and set(value) == {"status"}
                and value["status"] in _FIXTURE_APPIMAGE_ENTRY_STATES
            ):
                entries[inner] = dict(value)
                continue
            raise ValueError(
                f"appimage {path!r} entry {inner!r}: expected text or "
                f"{{'status': one of {_FIXTURE_APPIMAGE_ENTRY_STATES}}}, got {value!r}"
            )
        validated[path] = entries
    return validated


# The states a fixture PS2 BIOS header may declare short of the two strings:
# what RealMachine can report about a file it opened. "missing" is not among
# them — an absent file is modeled by not declaring it in ``files``.
_FIXTURE_PS2_BIOS_STATES = ("unreadable", "not-a-bios")
_FIXTURE_PS2_BIOS_FIELDS = ("romver", "serial")


def _validate_fixture_ps2_bios_headers(
    headers: Mapping[str, Mapping[str, object] | str],
    files: Mapping[str, tuple[ReadStatus, str | None]],
) -> dict[str, ps2_bios.Ps2BiosHeader | str]:
    """The declared header answers, built once by the reader's own code.

    A header describes a file's bytes, so its path must be a declared file,
    and one whose bytes can be read: a header beside ``unreadable`` would let
    a vector assert a read the machine it models never completes — the same
    refusal ``files`` makes for a digest on an unreadable file.
    """
    validated: dict[str, ps2_bios.Ps2BiosHeader | str] = {}
    for path, spec in headers.items():
        if path not in files:
            raise ValueError(
                f"ps2 bios header {path!r}: a header describes a file's bytes, and no file is declared there"
            )
        if files[path][0] == READ_UNREADABLE:
            raise ValueError(
                f"ps2 bios header {path!r}: an unreadable file states no header answer — its bytes are "
                "what cannot be read, so a real one answers 'unreadable' before any test"
            )
        if isinstance(spec, str):
            if spec not in _FIXTURE_PS2_BIOS_STATES:
                raise ValueError(
                    f"ps2 bios header {path!r}: a state must be one of {_FIXTURE_PS2_BIOS_STATES}, got {spec!r}"
                )
            validated[path] = spec
            continue
        validated[path] = _fixture_ps2_bios_header(path, spec)
    return validated


def _fixture_ps2_bios_header(path: str, spec: Mapping[str, object]) -> ps2_bios.Ps2BiosHeader:
    """The two strings the ROMDIR walk yields, checked to the lengths the core reads them at."""
    if set(spec) != set(_FIXTURE_PS2_BIOS_FIELDS):
        raise ValueError(
            f"ps2 bios header {path!r}: expected a state or an object with exactly "
            f"{_FIXTURE_PS2_BIOS_FIELDS}, got {spec!r}"
        )
    strings: dict[str, bytes] = {}
    for key in _FIXTURE_PS2_BIOS_FIELDS:
        value = spec[key]
        if not isinstance(value, str):
            raise ValueError(f"ps2 bios header {path!r}: {key} must be a string, got {value!r}")
        try:
            strings[key] = value.encode("latin-1")
        except UnicodeEncodeError as error:
            raise ValueError(
                f"ps2 bios header {path!r}: {key} must be one byte per character (latin-1), got {value!r}"
            ) from error
    if len(strings["romver"]) != ps2_bios.ROMVER_LENGTH:
        raise ValueError(
            f"ps2 bios header {path!r}: romver is the {ps2_bios.ROMVER_LENGTH} bytes the core reads, "
            f"got {len(strings['romver'])}"
        )
    if len(strings["serial"]) > ps2_bios.SERIAL_LENGTH or b"\x00" in strings["serial"]:
        raise ValueError(
            f"ps2 bios header {path!r}: serial is at most {ps2_bios.SERIAL_LENGTH} bytes and carries no NUL "
            "— what a C string read from fifteen bytes can hold"
        )
    return ps2_bios.Ps2BiosHeader.from_strings(strings["romver"], strings["serial"])


# The states a fixture archive may declare short of a member list: what
# RealMachine can report about a file it opened. "missing" is not among them —
# an absent archive is modeled by not declaring the file.
_FIXTURE_ARCHIVE_STATES = ("unreadable", "not-archive")
# And the states a fixture slave read may declare short of the structure. The
# container's own failures are not among them: they come from the archive
# declaration, so the two reads cannot contradict each other.
_FIXTURE_WHDLOAD_STATES = ("no-slave", "ambiguous", "slave-unreadable")
_FIXTURE_WHDLOAD_FIELDS = ("slave", "version", "name", "selected_by")


def _validate_fixture_archives(
    archives: Mapping[str, object],
    files: Mapping[str, tuple[ReadStatus, str | None]],
) -> dict[str, tuple[str, ...] | str]:
    """The declared listings — a member list, or the state a real read would report.

    An archive describes a file's bytes, so its path must be a declared file,
    and one whose bytes can be read: a member list beside ``unreadable`` would
    let a vector assert a walk the machine it models never completes.
    """
    validated: dict[str, tuple[str, ...] | str] = {}
    for path, spec in archives.items():
        _refuse_unlistable_archive(path, files)
        validated[path] = _fixture_archive(path, spec)
    return validated


def _refuse_unlistable_archive(
    path: str, files: Mapping[str, tuple[ReadStatus, str | None]]
) -> None:
    if path not in files:
        raise ValueError(
            f"archive {path!r}: a listing describes a file's bytes, and no file is declared there"
        )
    if files[path][0] == READ_UNREADABLE:
        raise ValueError(
            f"archive {path!r}: an unreadable file states no member list — its bytes are what "
            "cannot be read, so a real one answers 'unreadable' before any walk"
        )


def _fixture_archive(path: str, spec: object) -> tuple[str, ...] | str:
    if not isinstance(spec, str):
        return _fixture_members(path, spec)
    if spec not in _FIXTURE_ARCHIVE_STATES:
        raise ValueError(
            f"archive {path!r}: a state must be one of {_FIXTURE_ARCHIVE_STATES}, got {spec!r}"
        )
    return spec


def _fixture_members(path: str, spec: object) -> tuple[str, ...]:
    """A member list: archive-internal paths, which are relative and ``/``-separated."""
    if not isinstance(spec, list) or not all(isinstance(name, str) for name in spec):
        raise ValueError(f"archive {path!r}: expected a state or a list of member names, got {spec!r}")
    members = tuple(str(name) for name in spec)
    for name in members:
        segments = name.rstrip("/").split("/")
        if not name or name.startswith("/") or "\\" in name or any(
            segment in ("", ".", "..") for segment in segments
        ):
            raise ValueError(
                f"archive {path!r}: member {name!r} is not an archive-internal path — those are "
                "relative, '/'-separated, and name no empty, '.' or '..' segment"
            )
    return members


def _validate_fixture_whdload_slaves(
    slaves: Mapping[str, object],
    archives: Mapping[str, tuple[str, ...] | str],
    dirs: set[str],
    listing: Callable[[str], tuple[str, ...]],
) -> dict[str, tuple[str, int, str | None, str] | str]:
    """The declared slave reads — the fields the structure yields, or a state.

    A slave sits inside the container the core mounts, which is an archive
    whose member list is declared or a directory whose contents the fixture
    already holds — so the path names one of those, and a stated member must
    be in that container's listing. The real machine picks the member out of
    exactly that listing, so a fixture naming one that is not there would
    model a read no machine makes.
    """
    validated: dict[str, tuple[str, int, str | None, str] | str] = {}
    for path, spec in slaves.items():
        members = _fixture_container_members(path, archives, dirs, listing)
        if isinstance(spec, str):
            if spec not in _FIXTURE_WHDLOAD_STATES:
                raise ValueError(
                    f"whdload slave {path!r}: a state must be one of {_FIXTURE_WHDLOAD_STATES}, "
                    f"got {spec!r}"
                )
            validated[path] = spec
            continue
        validated[path] = _fixture_slave(path, spec, members)
    return validated


def _fixture_container_members(
    path: str,
    archives: Mapping[str, tuple[str, ...] | str],
    dirs: set[str],
    listing: Callable[[str], tuple[str, ...]],
) -> tuple[str, ...]:
    """What the container at *path* holds, however this fixture states it."""
    declared = archives.get(path)
    if isinstance(declared, tuple):
        return declared
    if path in dirs:
        return listing(path)
    raise ValueError(
        f"whdload slave {path!r}: a slave sits in the container the core mounts, and neither an "
        "archive with a member list nor a directory is declared there"
    )


def _fixture_slave(
    path: str, spec: object, members: tuple[str, ...]
) -> tuple[str, int, str | None, str]:
    """One declared structure, checked against what a real slave can state."""
    if not isinstance(spec, Mapping) or set(spec) != set(_FIXTURE_WHDLOAD_FIELDS):
        raise ValueError(
            f"whdload slave {path!r}: expected a state or an object with exactly "
            f"{_FIXTURE_WHDLOAD_FIELDS}, got {spec!r}"
        )
    member = spec["slave"]
    if member not in members:
        raise ValueError(f"whdload slave {path!r}: member {member!r} is not in this archive's listing")
    route = spec["selected_by"]
    if route not in whdload.SELECTION_ROUTES:
        raise ValueError(
            f"whdload slave {path!r}: selected_by must be one of {whdload.SELECTION_ROUTES}, got {route!r}"
        )
    version, name = _fixture_slave_fields(path, spec["version"], spec["name"])
    return str(member), version, name, str(route)


def _fixture_slave_fields(path: str, version: object, name: object) -> tuple[int, str | None]:
    """``ws_Version`` and ``ws_name``, checked against what a real structure can hold."""
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError(f"whdload slave {path!r}: version is the slave's own ws_Version, got {version!r}")
    if name is not None and not (isinstance(name, str) and name):
        raise ValueError(f"whdload slave {path!r}: name is ws_name or null, got {name!r}")
    if name is not None and version < whdload.NAMED_FROM_VERSION:
        raise ValueError(
            f"whdload slave {path!r}: a slave older than version {whdload.NAMED_FROM_VERSION} has no "
            "ws_name field at all, so it can state no name"
        )
    return version, name if isinstance(name, str) else None


class FixtureMachine:
    """A machine backed by plain data: files, directories, symlinks, core answers.

    ``files`` maps absolute paths to contents — a string is readable content;
    ``{"status": "unreadable"}`` / ``{"status": "invalid-text"}`` is a file
    that exists but yields that read outcome; ``{"md5": ..., "sha1": ...,
    "size": ...}`` is a binary blob that exists, reads as ``invalid-text``, and
    answers those values for ``file_digest`` / ``file_size``. A ``size`` may
    join a ``status``, because the two come from different reads on a real
    machine: ``{"status": "unreadable", "size": N}`` is the chmod-000 file,
    whose ``stat`` succeeds while its bytes do not. ``dirs`` lists directories
    that exist explicitly (parents of every known path are directories
    implicitly — the list is how *empty* directories are stated). ``symlinks``
    maps link paths to their targets (absolute, or relative to the link's
    directory). ``cores`` maps ``.so`` paths to core-answer objects
    (``{"library_name": ...}``); a path mapped to ``None`` is a core that is
    present but unloadable. ``appimages``, ``ps2_bios_headers``, ``archives``
    and ``whdload_slaves`` model four content reads at the seam rather than as
    bytes — see :meth:`read_appimage_text`, :meth:`read_ps2_bios_header`,
    :meth:`list_archive` and :meth:`read_whdload_slave`. The last two are a
    pair: ``archives`` maps an archive's path to its member list (or to
    ``"unreadable"`` / ``"not-archive"``), and ``whdload_slaves`` maps the
    same path to what the slave inside it states — ``{"slave": ..., "version":
    ..., "name": ..., "selected_by": ...}`` with a nullable name and the route
    that named the member, or ``"no-slave"`` / ``"ambiguous"`` /
    ``"slave-unreadable"``. A slave declaration must name a member the archive
    lists, so the two can never describe different machines.

    Two lists say what cannot be read, and which one applies is decided by one
    question: does the ``stat`` succeed?

    - ``unlistable`` — it does. The path *is* a directory and its contents
      cannot be read, which is what **a mode-000 directory answers about
      itself**: ``stat`` succeeds, so it is a directory, and the listing fails.
      Mode 111 behaves the same from outside, and shows the shape plainly — a
      wildcard finds nothing there while a name atlas already knows still
      answers. This is the list the resolver's failure paths run through,
      because reaching them means passing an "is it a directory?" check first.
    - ``inaccessible`` — it does not, so nothing about the path can be told.
      This is what the paths *below* a mode-000 directory answer, and what a
      mount point answers once the card behind it stops responding (``EIO`` on
      the stat). Declaring a directory declares its whole subtree, because the
      failing ``stat`` is the one every path below it needs first.

    So a mode-000 directory is ``unlistable``, and it is the wrong list that is
    tempting: putting the directory in ``inaccessible`` makes the directory
    itself unreachable, and a resolver then refuses it long before it would
    have tried to read it. Naming a path in both is not a mode-000 shorthand
    but a contradiction, and it fails construction rather than resolving to
    either — a precedence rule would be a documented way to describe the wrong
    machine quietly. Where a mode-000 directory's children matter, they are
    named in ``inaccessible`` individually; the directory itself is not.

    Every operation resolves the path it is given the way the module docstring
    states the kernel does — symlink components, ``.``, ``..`` and separator
    spellings alike — because the store is keyed by one path per file while a
    machine answers for every spelling that reaches it. So a link to a target
    that is not in the fixture is a *dead* link (``readlink`` shows it,
    ``path_kind`` says missing, exactly like the real thing), and a fixture
    cannot answer *missing* for a file the real machine hands over under a
    ``.``, a ``..``, a doubled separator or a trailing slash.
    """

    def __init__(
        self,
        files: Mapping[str, FixtureFileSpec],
        symlinks: Mapping[str, str] | None = None,
        cores: Mapping[str, Mapping[str, object] | None] | None = None,
        dirs: Iterable[str] | None = None,
        inaccessible: Iterable[str] | None = None,
        unlistable: Iterable[str] | None = None,
        appimages: Mapping[str, Mapping[str, object] | str] | None = None,
        ps2_bios_headers: Mapping[str, Mapping[str, object] | str] | None = None,
        archives: Mapping[str, object] | None = None,
        whdload_slaves: Mapping[str, object] | None = None,
    ) -> None:
        self._files, self._blobs = _index_fixture_files(files)
        self._symlinks = dict(symlinks or {})
        self._cores = dict(cores or {})
        self._appimages = _validate_fixture_appimages(appimages or {})
        self._ps2_bios_headers = _validate_fixture_ps2_bios_headers(ps2_bios_headers or {}, self._files)
        self._inaccessible = set(inaccessible or ())
        self._unlistable = set(unlistable or ())
        _refuse_both_unreadable_lists(self._inaccessible, self._unlistable)
        # A directory whose contents cannot be read is still a directory —
        # that is the whole difference from an inaccessible one, and what lets
        # a resolver walk up to it and then fail to look inside.
        self._dirs: set[str] = set(dirs or ()) | self._unlistable
        # Every ancestor of a known path is a directory.
        self._dirs |= _ancestor_dirs(
            (*self._files, *self._symlinks, *self._cores, *tuple(self._dirs), *self._inaccessible)
        )
        self._dirs.discard("")
        # The root is a directory on every machine, and no ancestor walk ever
        # reaches it (they stop at "/"). Without it a fixture answers *missing*
        # for "/" and for any spelling that climbs to it, and the walk below
        # relies on it: standing at "/" has to be standing in a directory.
        self._dirs.add("/")
        # Last, because a container may be a directory and a directory's own
        # listing is derived from everything above rather than declared.
        self._archives = _validate_fixture_archives(archives or {}, self._files)
        self._whdload_slaves = _validate_fixture_whdload_slaves(
            whdload_slaves or {}, self._archives, self._dirs, self._directory_members
        )

    def _resolve(self, path: str) -> _Landing:
        """Walk *path* component by component from ``/``, the way the kernel does.

        The one rule the failure spellings fall out of: before the walk steps
        *through* a place, that place must be a directory. So ``f.txt/`` and
        ``f.txt/../g`` are ``ENOTDIR`` while ``dir/`` and ``dir/./`` are the
        directory, and a ``..`` climbs from where the walk landed rather than
        from the spelling — ``link/..`` is the link target's parent.

        This is a resolution, not a normalization: collapsing the spelling
        first (``os.path.normpath``) would eat the component in front of a
        ``..`` even when that component is a symlink, and the kernel does the
        opposite. Nothing here consults the *final* component's kind; that is
        the caller's question, and a dangling final component is a dead link,
        not a refusal.

        A relative path names nothing: the real machine resolves it against the
        working directory of the process, which is not a fact about the machine
        being described, so a fixture has nowhere to start. (One reaches here —
        ``system_directory = "system"`` is checked for being a directory before
        it is refused for not being absolute.)

        This walk exists three times, for three different jobs:
        :func:`atlas.firmware.resolve_links` resolves a path *through* the seam
        for a caller that then reads it, and
        ``atlas.installations._resolve_symlink_chain`` also collects the links
        it traversed so a caveat can name them. They deliberately differ — only
        this one refuses to step through a non-directory, because only this one
        answers for the paths themselves — but a fidelity finding about
        symlinks, ``..`` or the hop limit belongs in all three, and they share
        :data:`SYMLINK_HOPS` so the boundary cannot drift.
        """
        if not path.startswith("/"):
            return _NOT_A_DIRECTORY
        parts = path.split("/")[1:]
        resolved = "/"
        hops = 0
        while parts:
            if resolved not in self._dirs:
                return _NOT_A_DIRECTORY
            segment = parts.pop(0)
            if segment in ("", "."):
                continue
            if segment == "..":
                resolved = os.path.dirname(resolved) or "/"
                continue
            candidate = os.path.join(resolved, segment)
            target = self._symlinks.get(candidate)
            if target is None:
                resolved = candidate
                continue
            hops += 1
            if hops > SYMLINK_HOPS:
                return _LOOPS
            resolved, parts = _stepped_onto_link(resolved, target, parts)
        return _Landing(resolved)

    def _resolve_parent(self, path: str) -> str | None:
        """Resolve symlinks in the parent components only — the final one stays.

        ``None`` when the spelling names no final component to report on: a
        trailing ``/``, ``.`` or ``..`` all make the kernel follow the last
        component instead of naming it, which is why a link to a directory
        answers its target when spelled bare and ``None`` when spelled with a
        trailing slash (observed on both).
        """
        parent, name = os.path.dirname(path), os.path.basename(path)
        if name in ("", ".", ".."):
            return None
        if parent in ("", "/"):
            return path
        landing = self._resolve(parent)
        return None if landing.path is None else os.path.join(landing.path, name)

    def read_appimage_text(self, path: str, inner_path: str) -> AppImageReadResult:
        """The modeled AppImage read — data in, the same outcomes RealMachine reports.

        Modeling lives beside the seam rather than in ``files``: what a real
        AppImage read needs (ELF layout, squashfs walk, a codec) is exactly
        what a fixture must NOT need, or every vector would carry a binary
        and only run where the codec exists. A path not declared here is a
        missing AppImage; an entry not declared is ``entry-missing``.
        """
        spec = self._appimages.get(path)
        if spec is None:
            return AppImageReadResult(READ_MISSING)
        if isinstance(spec, str):
            status: AppImageReadStatus = spec  # type: ignore[assignment]  # validated at construction
            return AppImageReadResult(status)
        entry = spec.get(inner_path)
        if entry is None:
            return AppImageReadResult(APPIMAGE_ENTRY_MISSING)
        if isinstance(entry, str):
            return AppImageReadResult(READ_OK, entry)
        entry_status: AppImageReadStatus = entry["status"]  # type: ignore[index,assignment]
        return AppImageReadResult(entry_status)

    def read_ps2_bios_header(self, path: str) -> Ps2BiosHeaderResult:
        """The modeled ROMDIR read — the two strings the walk yields in, the same outcomes RealMachine reports.

        Modeling lives beside the seam rather than in ``files`` for the reason
        ``appimages`` does: what the real read needs — a ROMDIR table inside a
        4 MiB blob — is exactly what a fixture must not carry. A path declared
        in ``ps2_bios_headers`` answers its header, built by the reader's own
        code from the ``romver`` and ``serial`` strings, or the state it
        names. A declared file with no entry there answers ``missing``: the
        fixture has no header to answer with, and answering ``not-a-bios``
        instead would let a vector that forgot the key prove a verdict nobody
        modeled — the folder route treats ``missing`` for a file it has just
        listed as a read that did not happen. An ``unreadable`` file answers
        ``unreadable`` before any test, as the real one does.
        """
        if self._is_inaccessible(path):
            return Ps2BiosHeaderResult(PS2_BIOS_UNREADABLE)
        resolved = self._resolve(path).path
        if resolved is None:
            return Ps2BiosHeaderResult(PS2_BIOS_MISSING)
        spec = self._ps2_bios_headers.get(resolved)
        if isinstance(spec, str):
            status: Ps2BiosHeaderStatus = spec  # type: ignore[assignment]  # validated at construction
            return Ps2BiosHeaderResult(status)
        if spec is not None:
            return Ps2BiosHeaderResult(PS2_BIOS_OK, spec)
        if resolved in self._dirs or self._files.get(resolved, (READ_MISSING, None))[0] == READ_UNREADABLE:
            return Ps2BiosHeaderResult(PS2_BIOS_UNREADABLE)
        return Ps2BiosHeaderResult(PS2_BIOS_MISSING)

    def list_archive(self, path: str) -> ArchiveListResult:
        """The modeled container listing — the same one rule, over data instead of bytes.

        An archive's member list is declared, because what a real one needs is
        the container's bytes and that is exactly what a fixture must not
        carry. A **directory** is not declared: its contents are already in
        the fixture, so the listing is derived from them and the two can never
        disagree. Anything else is not a container, which is also the answer a
        declared archive file with no entry gets — the one that keeps a vector
        that forgot the key from passing a file off as one that lists.
        """
        if self._is_inaccessible(path):
            return ArchiveListResult(ARCHIVE_UNREADABLE)
        resolved = self._resolve(path).path
        if resolved is None:
            return ArchiveListResult(ARCHIVE_MISSING)
        spec = self._archives.get(resolved)
        if isinstance(spec, str):
            status: ArchiveStatus = spec  # type: ignore[assignment]  # validated at construction
            return ArchiveListResult(status)
        if spec is not None:
            return ArchiveListResult(ARCHIVE_OK, spec)
        return self._unlisted_container(resolved)

    def _unlisted_container(self, resolved: str) -> ArchiveListResult:
        if resolved in self._unlistable:
            return ArchiveListResult(ARCHIVE_UNREADABLE)
        if resolved in self._dirs:
            return ArchiveListResult(ARCHIVE_OK, self._directory_members(resolved))
        if resolved not in self._files:
            return ArchiveListResult(ARCHIVE_MISSING)
        if self._files[resolved][0] == READ_UNREADABLE:
            return ArchiveListResult(ARCHIVE_UNREADABLE)
        return ArchiveListResult(ARCHIVE_NOT_ARCHIVE)

    def _directory_members(self, resolved: str) -> tuple[str, ...]:
        """Everything the fixture puts under one directory, relative and ``/``-separated."""
        prefix = resolved.rstrip("/") + "/"
        return tuple(
            sorted(
                known[len(prefix) :]
                for known in (*self._files, *self._cores)
                if known.startswith(prefix)
            )
        )

    def read_whdload_slave(self, path: str) -> WhdloadSlaveResult:
        """The modeled slave read — the mount first, the container's outcome, then the structure.

        The mount is resolved exactly as the real machine resolves it, so a
        ``.slave`` or an ``.info`` is modeled under the **drawer** it mounts
        rather than under itself. Everything the container can answer comes
        from the container, so the two reads cannot disagree about one file. A
        listed container with no entry here answers ``no-slave``, the weaker
        of the two claims a vector could want and therefore the safe fall.
        """
        container = whdload.mounted_container(path, self._is_directory)
        listed = self.list_archive(container)
        if listed.status != ARCHIVE_OK:
            return WhdloadSlaveResult(listed.status)
        resolved = self._resolve(container).path or container
        spec = self._whdload_slaves.get(resolved)
        custom = self._custom_of(resolved, listed.members)
        if spec is None:
            return WhdloadSlaveResult(WHDLOAD_NO_SLAVE, custom=custom)
        if isinstance(spec, str):
            status: WhdloadSlaveStatus = spec  # type: ignore[assignment]  # validated at construction
            return WhdloadSlaveResult(status, custom=custom)
        member, version, name, route = spec
        return WhdloadSlaveResult(WHDLOAD_OK, member, version, name, route, custom)

    def _custom_of(self, container: str, members: tuple[str, ...]) -> str | None:
        """The ``custom`` file's text, where a **directory** container holds one.

        A ``custom`` file is text a person wrote, so under a directory it is
        already in ``files`` and needs no modeling beside the seam. Inside an
        archive it would need the container's bytes, which a fixture does not
        carry — so a machine that has one is written with the drawer as the
        content path, which is the same mount and the same answer.
        """
        named = next((name for name in members if name.lower() == whdload.CUSTOM), None)
        if named is None or container not in self._dirs:
            return None
        return self._files.get(os.path.join(container, named), (READ_MISSING, None))[1]

    def _is_directory(self, path: str) -> bool:
        return self.path_kind(path) == KIND_DIRECTORY

    def read_text(self, path: str) -> ReadResult:
        if self._is_inaccessible(path):
            return ReadResult(READ_UNREADABLE)
        resolved = self._resolve(path).path
        if resolved is None:
            # ENOTDIR — the loop case was already answered above. The real
            # machine raises NotADirectoryError here, which it reports missing.
            return ReadResult(READ_MISSING)
        if resolved in self._files:
            status, text = self._files[resolved]
            return ReadResult(status, text)
        if resolved in self._cores:
            return ReadResult(READ_INVALID_TEXT)  # a binary is not text
        if resolved in self._dirs:
            return ReadResult(READ_UNREADABLE)
        return ReadResult(READ_MISSING)

    def path_kind(self, path: str) -> PathKind:
        if self._is_inaccessible(path):
            return KIND_INACCESSIBLE
        resolved = self._resolve(path).path
        if resolved is None:
            return KIND_MISSING  # ENOTDIR, as above
        if resolved in self._files or resolved in self._cores:
            return KIND_FILE
        if resolved in self._dirs:
            return KIND_DIRECTORY
        return KIND_MISSING

    def _is_inaccessible(self, path: str) -> bool:
        if self._at_or_under_inaccessible(path):
            return True
        landing = self._resolve(path)
        # A chain that never settles is ELOOP: the real machine fails to stat
        # it, which every operation here reports as inaccessible. ENOTDIR is
        # not that — it is an absent path, and each operation says so itself.
        return landing.loops or (
            landing.path is not None and self._at_or_under_inaccessible(landing.path)
        )

    def _at_or_under_inaccessible(self, path: str) -> bool:
        """Is *path* a declared inaccessible entry, or anything below one?

        Declaring a directory inaccessible declares its whole subtree: the
        ``stat`` that fails on it is the one every path below it needs first,
        and a mode-000 directory was observed to answer *inaccessible* for its
        children and its grandchildren alike. Stating each descendant instead
        would mean listing what a broken card contains in order to say it
        cannot be read.
        """
        return any(
            path == entry or path.startswith(entry.rstrip("/") + "/") for entry in self._inaccessible
        )

    def readlink(self, path: str) -> str | None:
        parent = self._resolve_parent(path)
        return None if parent is None else self._symlinks.get(parent)

    def file_size(self, path: str) -> int | None:
        if self._is_inaccessible(path):
            return None
        resolved = self._resolve(path).path
        if resolved is None:
            return None
        declared = self._blobs.get(resolved, {}).get("size")
        if isinstance(declared, int):
            return declared
        text = self._files.get(resolved, (READ_MISSING, None))[1]
        return len(text.encode("utf-8")) if text is not None else None

    def file_digest(self, path: str, algorithm: str) -> str | None:
        if algorithm not in DIGEST_ALGORITHMS or self._is_inaccessible(path):
            return None
        resolved = self._resolve(path).path
        if resolved is None:
            return None
        declared = self._blobs.get(resolved, {}).get(algorithm)
        if isinstance(declared, str):
            return declared
        text = self._files.get(resolved, (READ_MISSING, None))[1]
        if text is None:
            return None
        return hashlib.new(algorithm, text.encode("utf-8")).hexdigest()

    def query_core(self, so_path: str) -> CoreInfo | None:
        resolved = self._resolve(so_path).path
        spec = self._cores.get(resolved) if resolved is not None else None
        if not spec:
            return None
        name = spec.get("library_name")
        if not isinstance(name, str) or not name:
            return None
        version = spec.get("library_version")
        extensions = spec.get("valid_extensions")
        block_extract = spec.get("block_extract")
        return CoreInfo(
            library_name=name,
            library_version=version if isinstance(version, str) else None,
            valid_extensions=extensions if isinstance(extensions, str) else None,
            options=_parse_core_options(spec.get("options")),
            block_extract=block_extract if isinstance(block_extract, bool) else None,
        )

    def glob(self, pattern: str) -> GlobResult:
        return _GlobWalk(self._list_dir, self._is_dir, self._lexists).run(pattern)

    def _list_dir(self, path: str) -> list[str] | None:
        """The names directly under *path*, or ``None`` when it cannot be listed.

        A directory the fixture calls inaccessible is one whose ``stat`` fails,
        and nothing whose ``stat`` fails can be listed either — so the two
        answers come from the one declaration.
        """
        if self._is_inaccessible(path):
            return None
        landing = self._resolve(path)
        if landing.path is None or landing.path not in self._dirs:
            # Not there, or not a directory: a truthful empty listing.
            return []
        if landing.path in self._unlistable:
            return None
        prefix = landing.path.rstrip("/") + "/"
        names = {
            known[len(prefix) :].split("/", 1)[0]
            for known in (*self._files, *self._symlinks, *self._cores, *self._dirs, *self._inaccessible)
            if known.startswith(prefix)
        }
        names.discard("")
        return sorted(names)

    def _is_dir(self, path: str) -> bool | None:
        kind = self.path_kind(path)
        return None if kind == KIND_INACCESSIBLE else kind == KIND_DIRECTORY

    def _lexists(self, path: str) -> bool | None:
        # lstat semantics: a dead symlink is a name that exists.
        if self._is_inaccessible(path):
            return None
        if self.readlink(path) is not None:
            return True
        return self.path_kind(path) != KIND_MISSING
