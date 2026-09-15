"""WHDLoad content: which member PUAE launches, and the slave that names its saves.

WHDLoad redirects an installed program's writes to a base directory and gives
each program its own sub directory therein, whose name it derives from the
slave it was started with — "The name of the sub directory can be specified
using the SaveDir/K option or when not set will be derived by WHDLoad from the
infos of the Slave (ws_name or the Slave filename)" (WHDLoad manual,
``opt.html``, ``SavePath/K``). PUAE points that base at a volume of its own,
``SavePath=WHDSaves:`` (``whdload/WHDLoad.prefs:37`` at libretro/libretro-uae
0043cf9), so the per-game directory under the frontend's save root is named
from bytes inside the archive and from nothing else: an install archive's
directory, its slave's file name and its ``ws_name`` are three different
strings (the public Alien Breed install carries ``AlienBreedHD/``,
``AlienBreed.slave`` and ``Alien Breed``). This module reads those bytes.

**Which member is launched.** ``dc_get_image_type`` calls a member WHDLoad
content by its extension alone — ``lha``, ``slave`` or ``info``
(``libretro/libretro-dc.c:855-859``). Inside an extracted archive the core
takes the first such member whose slave or directory is really there
(``libretro-core.c:6332-6351``), and what it mounts as ``DH0:`` is the
archive itself for an ``.lha`` (:5706-5708) but, for a ``.slave`` or
``.info``, the directory beside it: the member's own directory joined with
its stem, falling back to that directory where no such subdirectory exists
(:5688-5700).

**Which slave is selected.** The boot script the core bakes in walks ``DH0:``
(``whdload/WHDLoad_files/S/Startup-Sequence:13`` changes into it; the same
script sits inside the baked ``WHDLoad.hdf``, so both WHDLoad modes select
alike). At :61-82 it does exactly this, and this module mirrors it:

- a file named ``load`` at the root replaces the whole launch, and no slave
  is named at all (:61-62);
- otherwise ``List #?.slav#?`` at the root — every entry whose name carries
  ``.slav``, matched the way AmigaDOS matches, without regard to case (:64);
- with none there and **no** ``.info`` at the root, the script descends into
  the root's directory and lists slaves there (:66-73);
- with none there and an ``.info`` at the root it stays put, and the only
  remaining branch takes a slave named after its own directory —
  ``<dir>/<dir>.slave`` (:74-81);
- what the script then *launches* is one step further than this module reads:
  with no custom options set it runs the icon beside the slave rather than the
  slave itself (:141-150), and only the custom path hands ``$SLAVE`` to
  WHDLoad directly (:181-187). Between them it tries ``game.slave``
  (:152-157), two Workbench icons (:159-170) and the drawer's own icon
  (:172-174), and reaches the interactive selector ``S:WBSelect`` (:176-177)
  only when no icon it could run stands closer. Upstream states the selector's
  reach more widely — "Selector will be launched always when there is no exact
  match for ``.slave``" (``README.md:396``) — and what this reading supports is
  the narrower claim.

``List … TO ENV:`` writes *every* match, so two candidate slaves or two
candidate directories leave a value the following ``CD`` and ``WHDLoad``
cannot use; what happens then is not established, and this module answers
"no slave" rather than picking one. The README's "The first one is selected"
(``README.md:360``) describes the intent; the mechanism does not say which
first is meant, so it is not stated as fact here.

**The slave itself** is "a standard AmigaDOS executable" that "MUST consist
of only ONE hunk" (WHDLoad autodoc, ``WHDLoad.Slave/--Overview--``), and the
``WHDLoadSlave`` structure sits at the start of it. The hunk file's own
layout is read the way UAE reads it (``sources/src/debugmem.c:1526-1576`` at
0043cf9): the identifier ``0x3F3``, a zero longword where a resident-library
list would be, the hunk-count table, then hunk blocks — ``0x3E9`` code,
``0x3EA`` data, ``0x3EB`` uninitialised — each with its length in longwords,
memory-attribute bits masked off the type and the size. The structure's own
fields are the autodoc's, big-endian, and everything from ``ws_name`` on
"only evaluated by WHDLoad if ``ws_Version`` is set to >= 10" — so a slave
older than that states no name here, and this module answers ``None`` rather
than substituting the file name the manual mentions, whose spelling (with or
without the extension) the manual does not give.
"""

from __future__ import annotations

import posixpath
import struct
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

# The extensions dc_get_image_type calls WHDLoad content (libretro-dc.c:855-859).
SUFFIXES = ("lha", "slave", "info")
# The two of those that are not containers themselves: the core mounts the
# drawer beside such a file rather than the file (libretro-core.c:5688-5700).
LAUNCH_SUFFIXES = ("slave", "info")
# The file whose presence hands the boot to the content's own script before
# any slave is looked for (whdload/WHDLoad_files/S/Startup-Sequence:15-39).
STARTUP_SEQUENCE = "s/startup-sequence"
# The file at a mounted root whose contents are appended to WHDLoad's own
# arguments, and which "always overrides WHDLoad.prefs" (README.md:364;
# Startup-Sequence:115-116, :183-185).
CUSTOM = "custom"
# The two settings that move an installed program's saves, in the prefs and in
# a `custom` file alike (WHDLoad manual, opt.html: SavePath/K and SaveDir/K).
_SAVE_PATH = "savepath"
_SAVE_DIR = "savedir"
_SAVE_KEYS = (_SAVE_PATH, _SAVE_DIR)
_QUOTES = ("\"", "'")
# What the core's baked prefs point SavePath at (whdload/WHDLoad.prefs:37) —
# the volume every mode this package states is built on.
SAVES_VOLUME = "whdsaves:"
PREFS = "WHDLoad.prefs"

# What the boot script looks for, lower-cased: the AmigaDOS pattern
# ``#?.slav#?`` is ".slav" with anything on either side.
_SLAVE_FRAGMENT = ".slav"
_INFO_SUFFIX = ".info"
_SLAVE_SUFFIX = ".slave"
# A file of this name at the root replaces the launch entirely (:61-62), and
# with it the whole block that would have read a `custom` file (:115-122).
LOAD = "load"
_LOAD = LOAD
# How a named slave was arrived at: the boot script's own search, or atlas's
# inference from an archive that offers WHDLoad exactly one.
BY_SCRIPT = "script"
BY_ONLY_SLAVE = "only-slave"
SELECTION_ROUTES = (BY_SCRIPT, BY_ONLY_SLAVE)
# What the core's archive walk passes over beside a leading dot: a playlist
# it may have generated itself.
_PLAYLIST_SUFFIX = "m3u"

_HUNK_HEADER = 0x3F3
_HUNK_CODE = 0x3E9
# The two high bits of a hunk type and of a hunk size are memory attributes,
# and a size carrying both is followed by an extra longword (debugmem.c
# :1550-1553, :1562).
_MEMORY_FLAGS = 0xC0000000
_LONG = 4
# UAE refuses a file whose hunk table is longer than this (debugmem.c:1537),
# and so does this reader — the bound is what keeps a corrupt length from
# being walked as a table.
_MAX_HUNKS = 1000

_WS_ID_OFFSET = 4
_WS_ID = b"WHDLOADS"
_WS_VERSION_OFFSET = 12
_WS_NAME_OFFSET = 36
# The version from which the fields beyond ws_ExpMem exist at all.
NAMED_FROM_VERSION = 10


class NotASlave(Exception):
    """The bytes are not a WHDLoad slave — the message says at which step."""


@dataclass(frozen=True, slots=True)
class Slave:
    """What a slave states about itself: the WHDLoad it needs, and its program's name.

    ``version`` is ``ws_Version``. ``name`` is ``ws_name``, the string
    WHDLoad shows in its splash window and derives the save sub directory
    from — ``None`` for a slave older than :data:`NAMED_FROM_VERSION`, which
    has no such field, and for one whose pointer or string is empty.
    """

    version: int
    name: str | None


def save_redirect(text: str) -> tuple[str | None, str | None]:
    """The ``SavePath`` and ``SaveDir`` a prefs file or a ``custom`` file states.

    Both are read the same way because WHDLoad reads them the same way: the
    prefs file states one setting per line and a ``custom`` file states them
    as arguments on one, so the text is taken as tokens either way. A ``;``
    opens a comment to the end of the line (``whdload/WHDLoad.prefs:1-6``),
    which is how the baked file carries its own explanations.

    Both spellings a keyword takes are read: ``SavePath=WHDSaves:`` and
    ``SavePath WHDSaves:``. [D] The second is how AmigaDOS ``ReadArgs``
    accepts a ``/K`` keyword, and a ``custom`` file's contents are appended
    to the command line, so it holds there; whether the prefs parser accepts
    it too is [O], and reading it as a setting either way is the safe
    direction — it can only make this refuse a redirection, never miss one.
    Quotes around a value are taken off, since that is how a value with
    spaces is written. Keys match without regard to case, and the last
    statement of a key is the one returned — which of two WHDLoad itself
    would take is stated nowhere, so a file saying one thing twice is [O].
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        _scan_settings(line.split(";", 1)[0].split(), found)
    return found.get(_SAVE_PATH), found.get(_SAVE_DIR)


def _scan_settings(tokens: list[str], found: dict[str, str]) -> None:
    """One line's tokens, in both spellings a keyword takes."""
    index = 0
    while index < len(tokens):
        key, sign, value = tokens[index].partition("=")
        if key.lower() in _SAVE_KEYS:
            if not sign and index + 1 < len(tokens):
                index += 1
                value = tokens[index]
            value, index = _quoted_value(value, tokens, index)
            found[key.lower()] = _unquoted(value)
        index += 1


def _quoted_value(value: str, tokens: list[str], index: int) -> tuple[str, int]:
    """A value the split cut in half, put back — a quoted path may hold spaces."""
    quote = value[:1]
    if quote not in _QUOTES or (len(value) > 1 and value.endswith(quote)):
        return value, index
    while index + 1 < len(tokens):
        index += 1
        value = f"{value} {tokens[index]}"
        if value.endswith(quote):
            break
    return value, index


def _unquoted(value: str) -> str:
    """A value with the quotes AmigaDOS uses around one that has spaces taken off."""
    quote = value[:1]
    if quote in _QUOTES and len(value) > 1 and value.endswith(quote):
        return value[1:-1]
    return value


def read_slave(data: bytes) -> Slave:
    """The structure at the start of a slave's single code hunk."""
    return _structure(_first_code_hunk(data))


def _first_code_hunk(data: bytes) -> bytes:
    """The bytes of the first code hunk — where the slave structure lives."""
    if _u32(data, 0) != _HUNK_HEADER:
        raise NotASlave("the file does not open with an AmigaDOS hunk header")
    if _u32(data, _LONG) != 0:
        # UAE's own loader reads only executables with no resident-library
        # list, and a slave is one; anything else is not what it would load.
        raise NotASlave("the hunk header names resident libraries, which a slave does not")
    first, last = _u32(data, 3 * _LONG), _u32(data, 4 * _LONG)
    if first > last or last - first + 1 > _MAX_HUNKS:
        raise NotASlave("the hunk header states a hunk range no executable has")
    position = 5 * _LONG
    for _ in range(last - first + 1):
        size = _u32(data, position)
        position += 2 * _LONG if size & _MEMORY_FLAGS == _MEMORY_FLAGS else _LONG
    if _u32(data, position) & ~_MEMORY_FLAGS != _HUNK_CODE:
        raise NotASlave("the first hunk of the file is not a code hunk")
    length = (_u32(data, position + _LONG) & ~_MEMORY_FLAGS) * _LONG
    hunk = data[position + 2 * _LONG : position + 2 * _LONG + length]
    if len(hunk) != length:
        raise NotASlave("the first code hunk is cut short by the end of the file")
    return hunk


def _structure(hunk: bytes) -> Slave:
    """Read ``ws_Version`` and ``ws_name`` out of the structure at the hunk's start."""
    if hunk[_WS_ID_OFFSET : _WS_ID_OFFSET + len(_WS_ID)] != _WS_ID:
        raise NotASlave("the code hunk does not open with the 'WHDLOADS' identifier")
    version = _u16(hunk, _WS_VERSION_OFFSET)
    if version < NAMED_FROM_VERSION:
        return Slave(version, None)
    pointer = _u16(hunk, _WS_NAME_OFFSET)
    return Slave(version, _relative_string(hunk, pointer) if pointer else None)


def _relative_string(hunk: bytes, pointer: int) -> str | None:
    """The NUL-terminated string a structure-relative pointer names."""
    end = hunk.find(b"\x00", pointer)
    if pointer >= len(hunk) or end < 0:
        raise NotASlave("ws_name points outside the slave's own hunk")
    return hunk[pointer:end].decode("latin-1") or None


def _u16(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise NotASlave(f"the slave structure ends before offset {offset}")
    return struct.unpack_from(">H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    if offset + _LONG > len(data):
        raise NotASlave(f"the hunk file ends before offset {offset}")
    return struct.unpack_from(">I", data, offset)[0]


# ---------------------------------------------------------------------------
# What the core launches out of an archive, and what it mounts for it.
# ---------------------------------------------------------------------------


def mounted_container(path: str, is_directory: Callable[[str], bool]) -> str:
    """What the core mounts as ``DH0:`` for one content path (:5688-5700, :5706-5711).

    An archive and a directory are mounted as themselves. A ``.slave`` or an
    ``.info`` is not: the core joins the file's own directory with its stem
    and mounts that where it is a directory, else the directory the file sits
    in — so a drawer icon mounts the drawer, and a slave lying loose mounts
    the folder around it.
    """
    stem, dot, suffix = path.rpartition(".")
    if not dot or suffix.lower() not in LAUNCH_SUFFIXES:
        return path
    return stem if is_directory(stem) else posixpath.dirname(path)


def extracted_root(names: Sequence[str]) -> str | None:
    """The prefix an extracted archive leaves mounted as ``DH0:``, or ``None``.

    The core's walk accepts WHDLoad members and mounts the drawer beside the
    one it took (:6332-6351); several such members that resolve to the *same*
    drawer are one mount whichever the walk met first, and only members
    resolving to different drawers make the answer depend on the order its
    own directory listing returned them in — which is what ``None`` says.
    With no WHDLoad member at all the walk falls to its last branch: the one
    entry it ended on where that is a directory (:6355-6362), and the whole
    extracted tree otherwise (:6296).
    """
    roots = {mounted_root(name, names) for name in accepted_members(names)}
    if roots:
        return roots.pop() if len(roots) == 1 else None
    entries = _top_level(names)
    walked = walked_entries(names)
    if len(walked) == 1 and walked[0] in entries.directories:
        return f"{walked[0]}/"
    return ""


def walked_entries(names: Sequence[str]) -> tuple[str, ...]:
    """The entries the core's archive walk classifies, in the listing's order.

    The walk reads the *extracted* tree's top level, so a member inside a
    directory contributes that directory's name and nothing deeper, and it
    passes over two kinds of name outright: one starting with a dot, and one
    ending in ``m3u`` (``libretro-core.c:6314`` at 0043cf9).
    """
    entries = _top_level(names)
    return tuple(name for name in (*entries.files, *entries.directories) if not _skipped(name))


def _skipped(name: str) -> bool:
    return name.startswith(".") or name.endswith(_PLAYLIST_SUFFIX)


def accepted_members(names: Sequence[str]) -> tuple[str, ...]:
    """Every WHDLoad member of an extracted archive the core would accept (:6332-6351).

    A member counts by its extension alone, except an ``.info``, which counts
    only where the drawer it belongs to or a same-named ``.slave`` is really
    there. The core takes the first it meets and stops looking, so a second
    never wins — but the walk is a directory listing in the filesystem's own
    order, so *which* is first is not a fact about the archive. All of them
    are returned and the caller decides; one is an answer, two are not.
    """
    entries = _top_level(names)
    return tuple(
        name
        for name in entries.files
        if not _skipped(name) and _accepted_whdload(name, entries)
    )


def _accepted_whdload(name: str, entries: "_TopLevel") -> bool:
    stem, _, suffix = name.rpartition(".")
    if suffix.lower() not in SUFFIXES:
        return False
    if suffix.lower() != "info":
        return True
    # An info is accepted only where its drawer or a same-named slave exists.
    return stem in entries.directories or f"{stem}{_SLAVE_SUFFIX}" in entries.files


def mounted_root(member: str, names: Sequence[str]) -> str:
    """The prefix of *names* the core mounts as ``DH0:`` for that member (:5688-5700).

    An ``.lha`` member is mounted as itself — the core hands the archive to
    UAE rather than a directory — which this cannot express, and the caller
    knows it holds a second container. It falls out as the empty prefix, the
    same answer a loose ``.slave`` gives, and the two are told apart by the
    member's own suffix rather than by this. For a ``.slave`` or an ``.info``
    the core joins the member's directory with the member's stem and mounts
    that where it is a directory, else its parent: inside an extracted
    archive that is the drawer beside the member, or the whole extracted tree
    where there is none.
    """
    stem = posixpath.splitext(member)[0]
    prefix = f"{stem}/"
    return prefix if any(name.startswith(prefix) for name in names) else ""


@dataclass(frozen=True, slots=True)
class Selection:
    """Which slave a mounted ``DH0:`` yields, and by which route.

    ``route`` is set exactly when ``slave`` is: :data:`BY_SCRIPT` where the
    boot script's own search named it, :data:`BY_ONLY_SLAVE` where atlas
    inferred it instead. ``ambiguous`` says the script's search *met*
    candidates and its own mechanism could not tell them apart, which is a
    different state from finding none — the caller states them differently.
    """

    slave: str | None = None
    route: str | None = None
    ambiguous: bool = False

    def __post_init__(self) -> None:
        if (self.slave is None) != (self.route is None):
            raise ValueError("Selection: a named slave carries the route that named it")


def select_slave(names: Iterable[str]) -> Selection:
    """Which slave a mounted ``DH0:`` yields — the script's choice, else the only one there.

    *names* are the paths under the mounted root, ``/``-separated. The script's
    own search runs first (:64-81). Where it names nothing, one inference
    stands in for it, and it is atlas's rather than the core's: **[D]** where
    the archive holds exactly one ``.slave`` member anywhere, that member is
    named even though the script did not select it, because whatever the
    launch turns out to be — one of the icons the script tries, or the
    interactive selector it falls to (:141-177; README.md:396) — WHDLoad has
    no other slave to be handed. The name follows from the set, not from the
    launch path, which is what makes this the sounder of the two routes: the
    script's own answer still has to be carried to WHDLoad by an icon whose
    tooltypes nothing here reads.

    The one outcome that inference is kept out of is a ``load`` file at the
    root: it replaces the launch with a command of the archive's own (:61-62),
    which atlas does not read and which need not run WHDLoad at all, so the
    premise does not hold there.
    """
    listing = list(names)
    if _has(_top_level(listing).files, _LOAD):
        return Selection()
    scripted = _script_selection(listing)
    if scripted.slave is not None:
        return scripted
    only = _only_slave(listing)
    return Selection(only, BY_ONLY_SLAVE) if only is not None else scripted


def _script_selection(listing: list[str]) -> Selection:
    """What the boot script's own search names (:64-81), or why it names nothing.

    ``List … TO ENV:`` writes *every* match into one variable (:64, :67), so
    two candidate slaves or two candidate directories leave a value the
    following ``CD`` and ``WHDLoad`` cannot use. Which one would win is not
    established, so the search reports that it could not tell them apart
    rather than picking.
    """
    root = _top_level(listing)
    here = _slaves(root.files)
    if here:
        return Selection(here[0], BY_SCRIPT) if len(here) == 1 else Selection(ambiguous=True)
    if len(root.directories) != 1:
        return Selection(ambiguous=len(root.directories) > 1)
    directory = root.directories[0]
    if any(name.lower().endswith(_INFO_SUFFIX) for name in root.files):
        return _named_after(directory, directory, listing)
    return _inside_first_directory(directory, listing)


def _inside_first_directory(directory: str, listing: list[str]) -> Selection:
    """With no ``.info`` at the root the script descends first, then searches (:68-73)."""
    inner = _top_level(_under(directory, listing))
    found = _slaves(inner.files)
    if found:
        if len(found) != 1:
            return Selection(ambiguous=True)
        return Selection(f"{directory}/{found[0]}", BY_SCRIPT)
    if len(inner.directories) != 1:
        return Selection(ambiguous=len(inner.directories) > 1)
    deeper = inner.directories[0]
    return _named_after(f"{directory}/{deeper}", deeper, listing)


def _named_after(directory: str, stem: str, listing: list[str]) -> Selection:
    """The last branch: a slave named exactly after the directory holding it (:77-78).

    The listing's own spelling is what comes back, because AmigaDOS compares
    without regard to case while the member has to be found by its name.
    """
    wanted = f"{directory}/{stem}{_SLAVE_SUFFIX}".lower()
    actual = next((name for name in listing if name.lower() == wanted), None)
    return Selection(actual, BY_SCRIPT) if actual is not None else Selection()


def _only_slave(listing: list[str]) -> str | None:
    """The one member WHDLoad could be run with, wherever in the archive it sits.

    A ``.slave`` suffix rather than the script's ``.slav`` fragment: the
    question here is what WHDLoad could be handed, and an icon beside a slave
    (``Game.slave.info``) is not that. Two of them name none — the set no
    longer decides.
    """
    slaves = sorted(name for name in listing if name.lower().endswith(_SLAVE_SUFFIX))
    return slaves[0] if len(slaves) == 1 else None


@dataclass(frozen=True, slots=True)
class _TopLevel:
    """One directory listing split the way ``List`` and ``List DIRS`` split it."""

    files: tuple[str, ...]
    directories: tuple[str, ...]


def _top_level(names: Iterable[str]) -> _TopLevel:
    """The entries directly in a listing: names without a separator, and first segments with one."""
    files: list[str] = []
    directories: list[str] = []
    for name in names:
        head, separator, _ = name.partition("/")
        target = directories if separator else files
        if head and head not in target:
            target.append(head)
    return _TopLevel(tuple(files), tuple(directories))


def _under(directory: str, names: Iterable[str]) -> list[str]:
    """The names inside one directory, relative to it."""
    prefix = f"{directory}/"
    return [name[len(prefix) :] for name in names if name.startswith(prefix)]


def _slaves(names: Iterable[str]) -> list[str]:
    """Every entry ``#?.slav#?`` matches, sorted so one listing gives one answer."""
    return sorted(name for name in names if _SLAVE_FRAGMENT in name.lower())


def _has(names: Iterable[str], wanted: str) -> bool:
    """Does the listing hold this name? AmigaDOS compares without regard to case."""
    lowered = wanted.lower()
    return any(name.lower() == lowered for name in names)
