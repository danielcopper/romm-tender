"""Whether ES-DE finds what a catalogue entry launches, and where — its own lookup, mirrored.

ES-DE resolves a launch command in two steps before it starts anything
(``FileData::launchGame``, ``es-app/src/FileData.cpp`` @ ES-DE v3.4.1):

1. **The emulator.** The token is the text between ``%EMULATOR_`` and the next
   ``%`` (``:2321-2326``). With no rules for it ES-DE refuses (``NO_RULES``,
   ``:2353-2355``, the popup at ``:1298-1311``). Otherwise every ``systempath``
   entry is tried before every ``staticpath`` entry, and the first that finds a
   file wins (``:2506-2593``):

   - ``systempath`` searches the directories of the process's ``PATH`` in
     order for ``<dir>/<entry>`` (``getPathToBinary``,
     ``es-core/src/utils/FileSystemUtil.cpp:353-370`` — the non-Flatpak
     branch; the RetroDECK build is not ``FLATPAK_BUILD``, and neither is the
     AppImage EmuDeck deploys, which is unforked ES-DE v3.4.1 itself).
   - ``staticpath`` splits a replacement command off at ``|``, expands ``~``
     against ES-DE's own home (:func:`atlas.esde.expand_home_path`), then
     ``%ESPATH%`` and ``%ROMPATH%``; a ``*`` takes the first match a directory
     listing yields (:func:`_matching_files`).
   - Either way a hit is ``isRegularFile || isSymlink`` (``FileSystemUtil.cpp:1018-1071``):
     a file once links are followed, or any symlink at all — a dead one
     included.

   A ``%PRECOMMAND_X%`` runs the same search over the same rules first
   (``:1166-1220``).
2. **The core.** A ``%CORE_X%`` names the core file as the text after
   ``%CORE_X%/`` up to the next space (or the closing quote), and the first
   ``corepath`` directory holding that name — file or symlink, the same test —
   is the core; none refuses the launch (``:1145-1161``, ``:1466-1568``).

A command with no ``%EMULATOR_X%`` token takes ES-DE's second method instead
of step 1 (``:2595-2649``): its first word — up to a space, or between quotes —
is the program, taken as a file where it names one and otherwise searched on
``PATH`` (:func:`_second_method`); a word without a leading ``/`` is a path
relative to the frontend's working directory.

What this module never does is touch the machine. Every probe goes through a
:class:`LaunchView`, which is the frontend's own view of the filesystem — for
RetroDECK the inside of its sandbox, for EmuDeck the host's own — and which
answers *could not tell* where that view cannot be established. A probe that cannot tell stops the walk,
because the entry after it might be the one ES-DE would take: the verdict is
then ``unestablished`` with the path that stopped it, never a guessed hit or
miss.

Where the view's ``PATH`` and working directory are assumed rather than read —
a frontend on the host takes them from whatever started it — every answer whose
search went through either says so (``launch-path-assumed``), and a caller can
hang its own note on a token's answers (EmuDeck's
``find-rules-rewritten-at-start``).

The cost is ``stat``, ``lstat`` and directory listings here, over find rules
the caller hands in already parsed: no core is probed, nothing is hashed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Literal, Protocol

from .esde import expand_home_path
from .find_rules import (
    AVAILABILITY_NOT_INSTALLED,
    AVAILABILITY_STARTABLE,
    AVAILABILITY_UNESTABLISHED,
    AVAILABILITIES,
    CAVEAT_CORE_RULES_MISSING,
    CAVEAT_EMULATOR_NOT_FOUND,
    CAVEAT_EMULATOR_RULES_MISSING,
    CAVEAT_FIND_RULES_UNREADABLE,
    CAVEAT_LAUNCH_PATH_ASSUMED,
    CAVEAT_LAUNCH_PATH_UNESTABLISHED,
    CAVEAT_LAUNCH_RESOLUTION_UNSUPPORTED,
    CAVEAT_RUN_GAME_DIFFERS,
    LAUNCH_RULES,
    LAYER_BUNDLED,
    LAYER_CUSTOM,
    CAVEAT_PROGRAM_NOT_FOUND,
    RULE_COMMAND,
    RULE_STATICPATH,
    RULE_SYSTEMPATH,
    Availability,
    EmulatorRules,
    FindRules,
    LaunchRule,
)
from .firmware import CAVEAT_CORE_NOT_INSTALLED
from .placement import Caveat

Probe = Literal["hit", "miss", "unknown"]
PROBE_HIT: Probe = "hit"
PROBE_MISS: Probe = "miss"
PROBE_UNKNOWN: Probe = "unknown"

_EMULATOR_OPENER = "%EMULATOR_"
_PRECOMMAND_OPENER = "%PRECOMMAND_"
_CORE_OPENER = "%CORE_"
_ESPATH = "%ESPATH%"
_ROMPATH = "%ROMPATH%"
_EMUPATH = "%EMUPATH%"
RUNINBACKGROUND = "%RUNINBACKGROUND%"
ENABLESHORTCUTS = "%ENABLESHORTCUTS%"
_QUOTED_CORE = '"' + _CORE_OPENER
# ``std::isspace``: what ES-DE trims off the front of a command after it
# removed a flag (``FileData.cpp:1063-1065``).
ISSPACE = " \t\n\v\f\r"


class LaunchView(Protocol):
    """The frontend's own view of the filesystem, which every probe of the lookup goes through.

    Paths are in the frontend's spelling throughout; translating them to
    something the host can read, and deciding where that cannot be done, is
    the view's whole job.
    """

    @property
    def app_id(self) -> str | None:
        """The sandbox the frontend runs in, ``None`` for one on the host."""
        ...

    @property
    def home(self) -> str:
        """What ES-DE substitutes for ``~`` — its ``getHomePath()``, which ``--home`` sets."""
        ...

    @property
    def search_path(self) -> tuple[str, ...]:
        """The directories of the frontend process's ``PATH``, in order."""
        ...

    @property
    def es_path(self) -> str:
        """``%ESPATH%``: the directory of the ES-DE binary (``getExePath``)."""
        ...

    @property
    def working_directory(self) -> str | None:
        """The directory ES-DE runs in, which a relative path is tested against — ``None`` where it is written
        nowhere. ES-DE never changes it (``chdir`` is called nowhere in ``es-app`` or ``es-core`` @ v3.4.1), so it is
        the directory whatever started the frontend left it in.
        """
        ...

    def rom_directory(self) -> str | None:
        """``%ROMPATH%``: ``getROMDirectory()``, trailing separator on — ``None`` where it cannot be told."""
        ...

    def found(self, path: str) -> Probe:
        """ES-DE's hit test on *path*: a regular file once links are followed, or any symlink."""
        ...

    def listing(self, directory: str) -> tuple[str, ...] | None:
        """The names in *directory* — empty when it is no directory, ``None`` when that cannot be told."""
        ...

    def executable(self, path: str) -> Probe:
        """run_game.sh's ``[ -x ]`` on *path*, read as "a file once links are followed" (no mode bits)."""
        ...

    def regular_file(self, path: str) -> Probe:
        """A regular file at *path* once every link is followed — what opening it finds, so a dead link, a
        directory and nothing at all are misses. Neither the read nor the executable bit is checked.
        """
        ...


@dataclass(frozen=True, slots=True)
class Launcher:
    """What ES-DE substitutes for an entry's ``%EMULATOR_X%`` token, and the rule entry that found it — or, for a
    command without one, the program its first word names.
    """

    path: str
    """The file the rule found, in the frontend's own spelling — for RetroDECK a path inside its sandbox, such as
    ``/app/retrodeck/components/dolphin/component_launcher.sh``; it is what ES-DE runs unless ``replacement_command`` is
    set.

    The path ES-DE checked, normalized the way it normalizes one (doubled
    separators collapsed, a trailing one dropped) and never shell-escaped:
    ES-DE escapes it for its own command line, a client running it does not.
    For a ``systempath`` hit it is the ``PATH`` directory joined to the entry,
    and for a ``command`` hit the first word as found: itself where it names
    a file, the working directory joined to it where it is relative, or the
    ``PATH`` directory joined to it.
    """
    replacement_command: str | None
    """The command ES-DE runs instead of ``path`` when the rule entry carries one after a ``|``, as written there;
    ``null`` when it carries none.

    ``path`` is then the file whose presence selected that command, and
    nothing ES-DE starts.
    """
    app_id: str | None
    """The Flatpak app whose sandbox ``path`` is spelled in — ``net.retrodeck.retrodeck`` for RetroDECK; ``null`` for a
    frontend that runs on the host, where the path is the host's own.
    """
    rule: LaunchRule
    """Which rule type found it: ``systempath`` (a search of the frontend's ``PATH``), ``staticpath`` (a path), or
    ``command`` (no rule: the command's own first word, as a file or on ``PATH``).
    """
    entry: str
    """The rule entry that matched, exactly as the find rules write it — before ``~``, ``%ESPATH%`` or a wildcard was
    expanded; for a ``command`` hit the command's first word as the command writes it, expanded no further.
    """

    def __post_init__(self) -> None:
        if self.rule not in LAUNCH_RULES:
            raise ValueError(f"Launcher: rule must be one of {LAUNCH_RULES}, got {self.rule!r}")


@dataclass(frozen=True, slots=True)
class LaunchResolution:
    """One entry's launch answer: the verdict, what was found, and the caveats that say why."""

    availability: Availability
    launcher: Launcher | None = None
    core_path: str | None = None
    caveats: tuple[Caveat, ...] = ()

    def __post_init__(self) -> None:
        if self.availability not in AVAILABILITIES:
            raise ValueError(
                f"LaunchResolution: availability must be one of {AVAILABILITIES}, got {self.availability!r}"
            )
        if (self.availability == AVAILABILITY_STARTABLE) != (self.launcher is not None):
            raise ValueError("LaunchResolution: a launcher stands exactly where the verdict is startable")


@dataclass(frozen=True, slots=True)
class LayeredFindRules:
    """The find rules ES-DE loads, with what the frontend could and atlas could not read of them.

    ``merged`` is the two layers merged ES-DE's way. ``bundled_read`` says
    whether the bundled layer was read and parsed; where it was not, only a
    name the custom layer defines (``custom_names``) can be answered, because
    that definition wins whole and the unread layer cannot change it.
    ``custom_unreadable`` is the custom layer that exists and could not be read
    or parsed — ES-DE skips it and reads on, so it is a note beside the
    verdict rather than a refusal. ``shipped`` is the file RetroDECK's own
    ``run_game.sh`` reads (``None`` where unread or not applicable).
    """

    merged: FindRules
    custom_names: frozenset[str]
    custom_core_names: frozenset[str]
    bundled_read: bool
    custom_unreadable: str | None = None
    shipped: FindRules | None = None


def _token(command: str, opener: str) -> str | None:
    """ES-DE's token reading: the text after *opener* up to the next ``%`` — ``None`` where it reads none.

    ``FileData.cpp:2313-2326``: the closing ``%`` is searched from one past the
    opening one, and an empty name is no token (``emulatorEntry != ""``,
    ``:2328``).
    """
    start = command.find(opener)
    if start == -1:
        return None
    end = command.find("%", start + 1)
    if end == -1:
        return None
    return command[start + len(opener) : end] or None


def es_replace(text: str, old: str, new: str) -> str:
    """``Utils::String::replace``: rescanned until *old* is gone, one pass where *new* holds *old*.

    ``es-core/src/utils/StringUtil.cpp:267-297`` @ v3.4.1.
    """
    if old == new:
        return text
    while old in text:
        text = text.replace(old, new)
        if old in new:
            break
    return text


def generic_path(path: str) -> str:
    """ES-DE's ``getGenericPath``: doubled separators collapsed, a trailing one dropped.

    ``FileSystemUtil.cpp:453-476`` @ v3.4.1.
    """
    path = path.replace("\\", "/")
    while "//" in path:
        path = path.replace("//", "/")
    while len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


def parent_path(path: str) -> str:
    """ES-DE's ``getParent``: the generic path up to its last separator (``FileSystemUtil.cpp:584-595``)."""
    generic = generic_path(path)
    offset = generic.rfind("/")
    return generic[:offset] if offset != -1 else generic


# ``getEscapedPath`` puts a backslash before each of these (``FileSystemUtil.cpp:478-506``).
_ESCAPED_CHARACTERS = "\\ '\"!$^&*(){}[]?;<>"


def escaped_path(path: str) -> str:
    """ES-DE's ``getEscapedPath`` on Unix: what it substitutes for a ``staticpath`` hit.

    Each listed character gets a backslash unless one already precedes it —
    the order of the list matters, because the backslash itself is first.
    """
    escaped = generic_path(path)
    for char in _ESCAPED_CHARACTERS:
        start = 0
        while (offset := escaped.find(char, start)) != -1:
            start = offset + 1
            if offset == 0 or escaped[offset - 1] != "\\":
                escaped = escaped[:offset] + "\\" + escaped[offset:]
                start += 1
    return escaped


def _matching_files(pattern: str, view: LaunchView, base: str | None = None) -> tuple[str, ...] | None:
    """ES-DE's ``getMatchingFiles`` (``FileSystemUtil.cpp:112-155``), ``None`` where the listing cannot be told.

    The wildcard is honored only in the last path component: a ``*`` at or
    before the parent's length matches nothing. The parent is listed in full
    and sorted (``getDirContent``, ``:62-110``), and every entry's whole path
    is matched against a regular expression built from the pattern — ``*``
    becomes ``.*``, parentheses and brackets are escaped, and nothing else is,
    so a ``.`` in the pattern matches any character there too. A pattern that
    does not compile matches nothing, as ES-DE catches the same failure. The
    expression is ``std::regex``'s ECMAScript grammar there and Python's here;
    the differences found are that ECMAScript's ``.`` also refuses ``\\r`` and
    the two Unicode line separators where Python's refuses only ``\\n``, and
    that a ``{`` which does not open a valid quantifier is not established to
    be read the same way by both. No file name a find rule targets carries
    those separators, and no entry of the shipped Linux rules at v3.4.1 or of
    RetroDECK's build carries a ``{``.

    A relative *pattern* is listed under *base*, the directory the frontend
    runs in: ``getDirContent`` iterates the relative parent the way the
    process's working directory resolves it, and spells each entry relative
    as the pattern is, so the match stays relative too.
    """
    star = pattern.find("*")
    parent = parent_path(pattern)
    if star <= len(parent):
        return ()
    names = view.listing(parent if base is None else f"{base}/{parent}")
    if names is None:
        return None
    expression = pattern.replace("*", ".*")
    for char in ")(][":
        expression = expression.replace(char, "\\" + char)
    try:
        compiled = re.compile(expression)
    except re.error:
        return ()
    prefix = "/" if parent == "/" else parent + "/"
    return tuple(path for path in sorted(prefix + name for name in names) if compiled.fullmatch(path))


@dataclass(frozen=True, slots=True)
class Found:
    """One hit: the launcher, and what ES-DE put into the command for it (``emulator.first``).

    ``substituted`` is the text as it went into the command: a ``systempath``
    hit's path as joined, unescaped (``FileData.cpp:2536-2541``); a
    ``staticpath`` hit's path through ``getEscapedPath``; or a ``|`` entry's
    replacement command, as written (``:2581-2590``). The second method puts
    nothing in place of a token, and ``substituted`` is then what it hands
    back, which the rest of the launch reads its ``%EMUDIR%`` off: the word
    through ``getEscapedPath``, or the escaped ``PATH`` directory joined to
    the word (``:2634-2642``).

    ``replaced`` is the one edit the second method makes to the command
    besides ``%ESPATH%``: a first word holding a ``*`` is replaced, wherever
    it stands, with its first match (``:2610-2616``) — the word as ES-DE read
    it, then the match. ``None`` where nothing was replaced.
    """

    launcher: Launcher
    substituted: str
    replaced: tuple[str, str] | None = None


@dataclass(frozen=True, slots=True)
class CommandParts:
    """What ES-DE substitutes into one command once its lookup has found everything it names.

    The texts the command is built from rather than the verdict:
    ``emulator`` for the ``%EMULATOR_X%`` token, ``precommand`` for a
    ``%PRECOMMAND_X%`` one, and ``core`` the file a ``%CORE_X%`` names, spelled
    as the ``corepath`` lookup tested it (``FileData.cpp:1500-1533``) and before
    ES-DE escapes one that holds a space.
    """

    emulator: Found
    precommand: Found | None = None
    core: str | None = None


@dataclass(frozen=True, slots=True)
class _Stopped:
    """A probe that could not tell, at the path ES-DE would have checked next."""

    path: str


def _search(rules: EmulatorRules, view: LaunchView) -> Found | _Stopped | None:
    """``findEmulator``'s two loops over one emulator's rules — ``None`` where nothing is found."""
    for entry in rules.system_paths:
        for directory in view.search_path:
            candidate = f"{directory}/{entry}"
            probe = view.found(candidate)
            if probe == PROBE_UNKNOWN:
                return _Stopped(candidate)
            if probe == PROBE_HIT:
                launcher = Launcher(generic_path(candidate), None, view.app_id, RULE_SYSTEMPATH, entry)
                return Found(launcher, candidate)
    for entry in rules.static_paths:
        outcome = _static_entry(entry, view)
        if outcome is not None:
            return outcome
    return None


def _static_entry(entry: str, view: LaunchView) -> Found | _Stopped | None:
    """One ``staticpath`` entry, expanded and checked the way ``FileData.cpp:2547-2593`` does."""
    path, _, replacement = entry.partition("|")
    path = expand_home_path(path, view.home)
    path = es_replace(path, _ESPATH, view.es_path)
    if _ROMPATH in path:
        rom_directory = view.rom_directory()
        if rom_directory is None:
            return _Stopped(path)
        path = es_replace(path, _ROMPATH, rom_directory)
    if "*" in path:
        files = _matching_files(path, view)
        if files is None:
            return _Stopped(parent_path(path))
        if files:
            path = files[0]
    probe = view.found(path)
    if probe == PROBE_UNKNOWN:
        return _Stopped(path)
    if probe == PROBE_MISS:
        return None
    # An empty replacement is no replacement: ES-DE runs the path it found
    # (``replaceCommand == ""``, :2581-2583).
    command = replacement or None
    launcher = Launcher(generic_path(path), command, view.app_id, RULE_STATICPATH, entry)
    return Found(launcher, command if command is not None else escaped_path(path))


def _before_lookup(command: str) -> str:
    """The edits ``launchGame`` makes before it looks anything up that move a first word: each flag removed with the
    blanks it leaves in front (``FileData.cpp:1057-1093``), and the quote before ``%CORE_`` dropped (``:1145-1151``).
    """
    for flag in (RUNINBACKGROUND, ENABLESHORTCUTS):
        if flag in command:
            command = es_replace(command, flag, "").lstrip(ISSPACE)
    return es_replace(command, _QUOTED_CORE, _CORE_OPENER)


def _split_first(command: str) -> tuple[str, str]:
    """The second method's program and what follows it: up to the first space, or between quotes where the command
    opens with one.

    ``FileData.cpp:2600-2608`` @ v3.4.1; a quote that is never closed takes
    the rest of the command.
    """
    if command.startswith('"'):
        rest = command[1:]
        end = rest.find('"')
        return (rest, "") if end == -1 else (rest[:end], rest[end + 1 :])
    end = command.find(" ")
    return (command, "") if end == -1 else (command[:end], command[end:])


def first_word(command: str) -> str:
    """The second method's program: up to the first space, or between quotes where the command opens with one."""
    return _split_first(command)[0]


def program_word(command: str) -> str:
    """The first word of *command* as the catalogue writes it — the flags ES-DE removes first removed, nothing
    expanded: what a ``command`` launcher's ``entry`` and ``program-not-found``'s ``program`` state.
    """
    return first_word(_before_lookup(command))


def word_after_program(command: str) -> str | None:
    """The word after :func:`program_word` in that same reading, split at blanks — ``None`` where none follows.

    What the program is handed first: for a ``/bin/bash <script>`` command,
    the script.
    """
    words = _split_first(_before_lookup(command))[1].split()
    return words[0] if words else None


@dataclass(frozen=True, slots=True)
class _ProgramSearch:
    """What the second method made of one command: the outcome, the word as written, and where it looked.

    ``searched`` holds the directories the word was joined to, in order — the
    working directory for a relative word, then the ``PATH`` directories —
    and ``assumed`` says the search passed through what the view may have
    assumed: any of them was probed.
    """

    outcome: Found | _Stopped | None
    program: str
    searched: tuple[str, ...] = ()
    assumed: bool = False


def _second_method(command: str, view: LaunchView) -> _ProgramSearch:
    """ES-DE's second method over a command naming no emulator token (``FileData.cpp:2595-2649`` @ v3.4.1).

    The command is the one ``launchGame`` hands ``findEmulator``: its flags
    removed and every ``~`` replaced with ES-DE's home (``:1164``). Then
    ``%ESPATH%`` is expanded across it, the first word read, and a ``*`` in
    that word replaced with the first match a listing yields
    (:func:`_matching_files`, ``FileSystemUtil.cpp:62-155``) — for a relative
    word, a listing under the working directory, which is then assumed too;
    without one stated the search stops at the word. The word is tested as a
    file — against the working directory where it is relative — and, where
    that misses, joined to each ``PATH`` directory in turn
    (``getPathToBinary``, ``FileSystemUtil.cpp:353-370``, which joins even a
    word holding a ``/``). The test is ES-DE's hit test either way, so a miss
    always went on to the ``PATH``. A probe that cannot tell stops the search,
    as a rule's does.
    """
    program = program_word(command)
    expanded = es_replace(expand_home_path(_before_lookup(command), view.home), _ESPATH, view.es_path)
    word = first_word(expanded)
    replaced: tuple[str, str] | None = None
    if "*" in word:
        relative = _relative(word, view)
        base = view.working_directory if relative else None
        if relative and base is None:
            return _ProgramSearch(_Stopped(word), program)
        files = _matching_files(word, view, base)
        if files is None:
            parent = parent_path(word)
            return _ProgramSearch(_Stopped(parent if base is None else f"{base}/{parent}"), program, assumed=relative)
        if files:
            replaced = (word, files[0])
            word = files[0]
    return _locate(word, program, replaced, view)


def _relative(word: str, view: LaunchView) -> bool:
    """Whether ES-DE resolves *word* against its working directory: a path with no leading ``/`` (and not under
    ``%ESPATH%``, which the view expands to an absolute directory or keeps as the image's mount).
    """
    return bool(word) and not word.startswith(("/", view.es_path))


def _locate(word: str, program: str, replaced: tuple[str, str] | None, view: LaunchView) -> _ProgramSearch:
    """The word as a file, then on ``PATH`` (``FileData.cpp:2634-2643``): the first hit, or every place tried."""
    searched: list[str] = []
    candidate = word
    if _relative(word, view):
        directory = view.working_directory
        if directory is None:
            return _ProgramSearch(_Stopped(word), program)
        searched.append(directory)
        candidate = f"{directory}/{word}"
    probe = view.found(candidate)
    if probe == PROBE_UNKNOWN:
        return _ProgramSearch(_Stopped(candidate), program, tuple(searched), bool(searched))
    if probe == PROBE_HIT:
        launcher = Launcher(generic_path(candidate), None, view.app_id, RULE_COMMAND, program)
        return _ProgramSearch(Found(launcher, escaped_path(word), replaced), program, tuple(searched), bool(searched))
    for directory in view.search_path:
        searched.append(directory)
        candidate = f"{directory}/{word}"
        probe = view.found(candidate)
        if probe == PROBE_UNKNOWN:
            return _ProgramSearch(_Stopped(candidate), program, tuple(searched), assumed=True)
        if probe == PROBE_HIT:
            launcher = Launcher(generic_path(candidate), None, view.app_id, RULE_COMMAND, program)
            found = Found(launcher, f"{escaped_path(directory)}/{word}", replaced)
            return _ProgramSearch(found, program, tuple(searched), assumed=True)
    return _ProgramSearch(None, program, tuple(searched), assumed=True)


@dataclass(frozen=True, slots=True)
class _CoreReference:
    """What a ``%CORE_X%`` names: the rule's name and the core file — ``core_so`` ``None`` for a malformed reference."""

    name: str
    core_so: str | None


def _core_reference(command: str) -> _CoreReference | None:
    """The ``%CORE_X%`` of *command* read ES-DE's way, ``None`` where it names none.

    A ``"`` right before the token makes the closing quote the end of the file
    name instead of the next space (``FileData.cpp:1145-1158``, ``:1487-1498``).
    """
    quoted = '"' + _CORE_OPENER in command
    text = command.replace('"' + _CORE_OPENER, _CORE_OPENER)
    start = text.find(_CORE_OPENER)
    if start == -1:
        return None
    end = text.find("%", start + len(_CORE_OPENER))
    if end == -1 or end == start + len(_CORE_OPENER):
        return None
    name = text[start + len(_CORE_OPENER) : end]
    separator = text.find('"', end) if quoted else -1
    if separator == -1:
        separator = text.find(" ", end)
    return _CoreReference(name, text[end + 2 : separator] if separator != -1 else None)


def unsupported(kind: str, reason: str) -> LaunchResolution:
    """The answer for every entry of an arrangement this evaluation does not cover, naming its kind."""
    return LaunchResolution(
        AVAILABILITY_UNESTABLISHED,
        caveats=(
            Caveat(
                CAVEAT_LAUNCH_RESOLUTION_UNSUPPORTED,
                f"whether the frontend finds what this entry launches is not evaluated here: {reason}",
                {"installation": kind},
            ),
        ),
    )


def command_unsupported(reason: str) -> LaunchResolution:
    """The answer for one command whose shape this evaluation does not follow — a fact of the row, so no kind."""
    return LaunchResolution(
        AVAILABILITY_UNESTABLISHED,
        caveats=(
            Caveat(
                CAVEAT_LAUNCH_RESOLUTION_UNSUPPORTED,
                f"whether the frontend finds what this entry launches is not evaluated here: {reason}",
                {},
            ),
        ),
    )


def _path_unestablished(path: str) -> LaunchResolution:
    return LaunchResolution(
        AVAILABILITY_UNESTABLISHED,
        caveats=(
            Caveat(
                CAVEAT_LAUNCH_PATH_UNESTABLISHED,
                f"ES-DE would check {path} next, and atlas cannot tell what the frontend sees there — the "
                "rule entries after it might decide, so no verdict is stated",
                {"path": path},
            ),
        ),
    )


def _bundled_unread() -> LaunchResolution:
    return LaunchResolution(
        AVAILABILITY_UNESTABLISHED,
        caveats=(
            Caveat(
                CAVEAT_FIND_RULES_UNREADABLE,
                "the bundled es_find_rules.xml could not be read or parsed, and it is the layer that "
                "defines this name — what ES-DE would look for is unknown",
                {"layer": LAYER_BUNDLED},
            ),
        ),
    )


def _rules_missing(emulator_token: str) -> LaunchResolution:
    return LaunchResolution(
        AVAILABILITY_UNESTABLISHED,
        caveats=(
            Caveat(
                CAVEAT_EMULATOR_RULES_MISSING,
                f"es_find_rules.xml holds no rules for {emulator_token} — ES-DE refuses the launch "
                "(\"MISSING EMULATOR FIND RULES CONFIGURATION\"), and where it would look is not stated anywhere",
                {"emulator_token": emulator_token},
            ),
        ),
    )


def _core_rules_missing(core_token: str) -> LaunchResolution:
    return LaunchResolution(
        AVAILABILITY_UNESTABLISHED,
        caveats=(
            Caveat(
                CAVEAT_CORE_RULES_MISSING,
                f"es_find_rules.xml holds no corepath rules for the core token {core_token} — ES-DE refuses the "
                "launch (\"MISSING CORE CONFIGURATION\", FileData.cpp:1466-1480), and where the core would be is "
                "stated nowhere",
                {"core_token": core_token},
            ),
        ),
    )


def _not_found(emulator_token: str, rules: EmulatorRules) -> LaunchResolution:
    return LaunchResolution(
        AVAILABILITY_NOT_INSTALLED,
        caveats=(
            Caveat(
                CAVEAT_EMULATOR_NOT_FOUND,
                f"none of the find rules for {emulator_token} matches a file the frontend can see — ES-DE "
                "refuses the launch (\"COULDN'T FIND EMULATOR\")",
                {"emulator_token": emulator_token, "searched": (*rules.system_paths, *rules.static_paths)},
            ),
        ),
    )


def _program_not_found(search: _ProgramSearch) -> LaunchResolution:
    return LaunchResolution(
        AVAILABILITY_NOT_INSTALLED,
        caveats=(
            Caveat(
                CAVEAT_PROGRAM_NOT_FOUND,
                f"the command names no emulator token, and its first word {search.program} is neither a file the "
                "frontend can see nor a program on its PATH — ES-DE refuses the launch (\"COULDN'T FIND EMULATOR\", "
                "FileData.cpp:1354-1356)",
                {"program": search.program, "searched": search.searched},
            ),
        ),
    )


def _emulator_rules(token: str, rules: LayeredFindRules) -> EmulatorRules | LaunchResolution:
    """The rules ES-DE holds for *token*, or the answer that says why there are none to search."""
    if not rules.bundled_read and token not in rules.custom_names:
        return _bundled_unread()
    found = rules.merged.emulators.get(token)
    if found is None or found.empty:
        return _rules_missing(token)
    return found


def _find(token: str, rules: LayeredFindRules, view: LaunchView) -> Found | LaunchResolution:
    """The launcher ES-DE finds for *token*, or the answer that says it finds none."""
    emulator_rules = _emulator_rules(token, rules)
    if isinstance(emulator_rules, LaunchResolution):
        return emulator_rules
    outcome = _search(emulator_rules, view)
    if outcome is None:
        return _not_found(token, emulator_rules)
    if isinstance(outcome, _Stopped):
        return _path_unestablished(outcome.path)
    return outcome


class LaunchLookup:
    """ES-DE's lookup over one answer's snapshot — the find rules read once, each emulator searched once.

    Every entry of one answer is resolved against the same rules and the same
    view, so the search for a token is the same search however many entries
    name it: a system's libretro rows all launch RetroArch, and its search is
    made once and shared (as is run_game.sh's reading of it). Built per answer
    and discarded with it, so a second answer reads the machine again.

    *path_assumed* says the view's ``search_path`` and ``working_directory``
    are assumed rather than read: every answer whose search went through
    either then carries ``launch-path-assumed`` beside its verdict.
    *token_notes* are notes the caller holds about one emulator token's
    rules, each riding beside every answer for a command naming that token.
    """

    def __init__(
        self,
        rules: LayeredFindRules,
        view: LaunchView,
        *,
        path_assumed: bool = False,
        token_notes: Mapping[str, Caveat] | None = None,
    ) -> None:
        self._rules = rules
        self._view = view
        self._path_assumed = path_assumed
        self._token_notes: Mapping[str, Caveat] = token_notes or {}
        self._found: dict[str, Found | LaunchResolution] = {}
        self._notes: dict[str, Caveat | None] = {}
        self._programs: dict[str, _ProgramSearch] = {}

    def _find(self, token: str) -> Found | LaunchResolution:
        if token not in self._found:
            self._found[token] = _find(token, self._rules, self._view)
        return self._found[token]

    def _program(self, command: str) -> _ProgramSearch:
        if command not in self._programs:
            self._programs[command] = _second_method(command, self._view)
        return self._programs[command]

    def _run_game_note(self, token: str, launcher: Launcher) -> Caveat | None:
        if token not in self._notes:
            self._notes[token] = _run_game_note(token, launcher, self._rules.shipped, self._view)
        return self._notes[token]

    def resolve(self, command: str, *, loads_core: bool) -> LaunchResolution:
        """Whether ES-DE finds what *command* launches — the emulator, then the core it names.

        *loads_core* says the entry is a libretro one, whose verdict needs the
        core found too: a libretro command naming its core by a path rather
        than through ``%CORE_X%`` is one ES-DE never checks, so it is not
        evaluated either (``launch-resolution-unsupported``). So is a
        command carrying ``%EMUPATH%`` (checked against the emulator's
        directory, ``:1399-1464``) or a malformed ``%CORE_X%``.

        A command without an ``%EMULATOR_X%`` token goes through ES-DE's
        second method (``FileData.cpp:2595-2649``, :func:`_second_method`):
        its first word, taken as a file or searched on ``PATH``. A hit is
        ``startable`` with a ``command`` launcher, a miss ``not-installed``
        with ``program-not-found``, and a relative word the view has no
        working directory for ``launch-path-unestablished``. Such a command
        that also carries a ``%PRECOMMAND_X%`` is not evaluated: ES-DE puts
        the pre-command's path in first, and the word it then reads is that
        path, which this does not follow. RetroDECK's ``run_game.sh`` hands
        such a command to ``eval`` (``libexec/run_game.sh:164``) with no
        lookup of its own, so no ``run-game-differs`` is stated for it. That
        start would differ only where the first word holds ``%ESPATH%``,
        which run_game.sh never expands, or a ``*``, which the shell expands
        to every match where ES-DE takes the first; RetroDECK's deployed
        catalogue holds no command without a token at all.

        ES-DE looks for the core of any command naming a ``%CORE_X%``,
        whatever the entry is (``:1145-1161``, ``:1466-1568``), so a standalone
        entry naming one waits on that core too: its verdict is the one the
        launcher and the core reach together. The core's path is stated on a
        libretro entry alone; on any other it stays ``None``.

        When rules exist and none matches, ES-DE does not stop there: it falls
        through to that second method with the token still in the command —
        the first word, up to a space or between quotes (``:2600-2608``), is
        taken as a file or searched on ``PATH`` (``:2633-2643``). A command
        opening with the token tries the token itself and finds nothing; one
        opening with something else (``env …``) "finds" that word, and the
        launch then fails on the literal ``%EMULATOR_X%`` it runs. Either way
        nothing the rules name was found, which is what decides whether the
        launch can work, so the verdict here is ``not-installed`` with
        ``emulator-not-found``.

        A ``%PRECOMMAND_X%`` is searched first, over the same rules
        (``:1166-1220``), and its refusal is the answer: the reason caveat's
        ``emulator_token`` then names the pre-command (``WINE``), not the
        emulator the entry launches.
        """
        answer = self._resolve(command, loads_core=loads_core)
        notes = self._command_notes(command)
        custom = _custom_note(self._rules)
        if custom is not None:
            notes.append(custom)
        return _with_notes(answer, notes)

    def command_parts(self, command: str, *, loads_core: bool) -> CommandParts | None:
        """What ES-DE substitutes into *command* — ``None`` where :meth:`resolve` answers anything but startable.

        The same lookup over the same snapshot and the same cache: a question
        that takes the command apart asks this after the entry's verdict, and
        it cannot find something the verdict did not.
        """
        return self._evaluate(command, loads_core=loads_core)[1]

    def _resolve(self, command: str, *, loads_core: bool) -> LaunchResolution:
        return self._evaluate(command, loads_core=loads_core)[0]

    def _command_notes(self, command: str) -> list[Caveat]:
        """The notes the caller asked for beside *command*'s answer: what was assumed, then the token's."""
        notes: list[Caveat] = []
        token = _token(command, _EMULATOR_OPENER)
        if token is None:
            if self._path_assumed and _takes_second_method(command) and self._program(command).assumed:
                notes.append(_path_assumed(self._view))
            return notes
        if self._path_assumed and any(self._through_path(name) for name in self._searched(command, token)):
            notes.append(_path_assumed(self._view))
        token_note = self._token_notes.get(token)
        if token_note is not None:
            notes.append(token_note)
        return notes

    def _searched(self, command: str, token: str) -> list[str]:
        """The tokens :meth:`_evaluate` searched for *command*, in its order: a refused pre-command stops it there."""
        if _EMUPATH in command:
            return []
        precommand = _token(command, _PRECOMMAND_OPENER)
        if precommand is None:
            return [token]
        if isinstance(self._find(precommand), LaunchResolution):
            return [precommand]
        return [precommand, token]

    def _through_path(self, token: str) -> bool:
        """Whether *token*'s search ran over ``search_path``: it has ``systempath`` entries, which are tried first."""
        rules = _emulator_rules(token, self._rules)
        return isinstance(rules, EmulatorRules) and bool(rules.system_paths)

    def _evaluate(self, command: str, *, loads_core: bool) -> tuple[LaunchResolution, CommandParts | None]:
        if _EMUPATH in command:
            return command_unsupported("the command uses %EMUPATH%, which this evaluation does not follow"), None
        token = _token(command, _EMULATOR_OPENER)
        if token is None:
            return self._evaluate_program(command, loads_core=loads_core)
        precommand = _token(command, _PRECOMMAND_OPENER)
        before: Found | None = None
        if precommand is not None:
            found_before = self._find(precommand)
            if isinstance(found_before, LaunchResolution):
                return found_before, None
            before = found_before
        found = self._find(token)
        if isinstance(found, LaunchResolution):
            return found, None
        return self._with_core(command, found, before, loads_core=loads_core, token=token)

    def _evaluate_program(self, command: str, *, loads_core: bool) -> tuple[LaunchResolution, CommandParts | None]:
        """A command without an emulator token: ES-DE's second method, then the core it names."""
        if not _takes_second_method(command):
            reason = (
                "the command names a %PRECOMMAND_…% and no %EMULATOR_…% token, so the first word ES-DE reads is the "
                "pre-command's path, which this evaluation does not follow"
            )
            return command_unsupported(reason), None
        search = self._program(command)
        if search.outcome is None:
            return _program_not_found(search), None
        if isinstance(search.outcome, _Stopped):
            return _path_unestablished(search.outcome.path), None
        return self._with_core(command, search.outcome, None, loads_core=loads_core, token=None)

    def _with_core(
        self, command: str, found: Found, before: Found | None, *, loads_core: bool, token: str | None
    ) -> tuple[LaunchResolution, CommandParts | None]:
        """The verdict once the program is found: the core the command names, then run_game.sh's reading of *token*."""
        reference = _core_reference(command)
        if reference is None and loads_core:
            reason = "the core is named by a path rather than through %CORE_…%, which ES-DE never checks"
            return command_unsupported(reason), None
        core_file: str | None = None
        if reference is not None:
            core = _find_core(reference, found, self._rules, self._view)
            if isinstance(core, LaunchResolution):
                return core, None
            core_file = core
        core_path = generic_path(core_file) if loads_core and core_file is not None else None
        note = None if token is None else self._run_game_note(token, found.launcher)
        notes = () if note is None else (note,)
        resolution = LaunchResolution(AVAILABILITY_STARTABLE, found.launcher, core_path, notes)
        return resolution, CommandParts(found, before, core_file)


def _takes_second_method(command: str) -> bool:
    """Whether a command without an emulator token is one the second method is followed for: no ``%EMUPATH%``,
    no pre-command.
    """
    return _EMUPATH not in command and _PRECOMMAND_OPENER not in command


def _find_core(
    reference: _CoreReference, found: Found, rules: LayeredFindRules, view: LaunchView
) -> str | LaunchResolution:
    """The core file the ``corepath`` rules find, as ES-DE tested it, or the answer that says they find none."""
    if not rules.bundled_read and reference.name not in rules.custom_core_names:
        return _bundled_unread()
    paths = rules.merged.cores.get(reference.name, ())
    if not paths:
        return _core_rules_missing(reference.name)
    if reference.core_so is None:
        return command_unsupported("the command's %CORE_…% reference has no end ES-DE can read")
    for path in paths:
        candidate = expand_home_path(f"{path}/{reference.core_so}", view.home)
        if _EMUPATH in candidate:
            candidate = candidate.replace(_EMUPATH, parent_path(found.substituted), 1)
        if _ESPATH in candidate:
            candidate = candidate.replace(_ESPATH, view.es_path, 1)
        probe = view.found(candidate)
        if probe == PROBE_UNKNOWN:
            return _path_unestablished(candidate)
        if probe == PROBE_HIT:
            return candidate
    return LaunchResolution(
        AVAILABILITY_NOT_INSTALLED,
        caveats=(
            Caveat(
                CAVEAT_CORE_NOT_INSTALLED,
                f"none of the corepath directories holds {reference.core_so} — ES-DE refuses the launch "
                "(\"COULDN'T FIND EMULATOR CORE FILE\")",
                {"core_so": reference.core_so, "searched": paths},
            ),
        ),
    )


def _custom_note(rules: LayeredFindRules) -> Caveat | None:
    """The skipped custom layer, stated beside the answer where one was skipped — ``None`` where none was."""
    if rules.custom_unreadable is None:
        return None
    return Caveat(
        CAVEAT_FIND_RULES_UNREADABLE,
        f"the custom find rules at {rules.custom_unreadable} exist and could not be read or parsed — "
        "ES-DE skips such a file and reads the bundled one, and so does this answer",
        {"layer": LAYER_CUSTOM},
    )


def _path_assumed(view: LaunchView) -> Caveat:
    """What the search assumed about the frontend's process: its ``PATH``, and the directory it runs in — both
    written nowhere on disk.

    A view that states no working directory gives the note ``search_path``
    alone: a caveat's data carries no ``null``. No frontend does so today —
    EmuDeck's states one, and RetroDECK's ``PATH`` is read, so it never gets
    the note.
    """
    message = (
        "the search ran over an assumed PATH or working directory — the frontend process's own are written "
        "nowhere on disk, so a frontend started with others may find another program, or none"
    )
    if view.working_directory is None:
        return Caveat(CAVEAT_LAUNCH_PATH_ASSUMED, message, {"search_path": view.search_path})
    return Caveat(
        CAVEAT_LAUNCH_PATH_ASSUMED,
        message,
        {"search_path": view.search_path, "working_directory": view.working_directory},
    )


def _with_notes(answer: LaunchResolution, notes: list[Caveat]) -> LaunchResolution:
    """*answer* with *notes* stated after its own caveats."""
    if not notes:
        return answer
    return LaunchResolution(answer.availability, answer.launcher, answer.core_path, (*answer.caveats, *notes))


# run_game.sh's own token pattern (``libexec/run_game.sh:232``): only these
# characters, so a token spelled with a ``-`` is never searched there.
_RUN_GAME_TOKEN = re.compile(r"[A-Z0-9_]+")
# The one token run_game.sh substitutes without a search (``:293``).
_RUN_GAME_SHELL = ("OS-SHELL", "/bin/sh")
# What an entry must not hold for run_game.sh's line-wise read to take it
# (``:383``, ``:394``): xmllint prints an element per line and escapes markup,
# so an entry spread over lines, or spelling ``&``, ``<`` or ``>``, comes back
# as text that is not the entry.
_RUN_GAME_UNREADABLE = ("\n", "&", "<", ">")


def _run_game_pick(token: str, shipped: FindRules, view: LaunchView) -> str | None:
    """What RetroDECK's ``find_emulator`` would run for *token* — ``""`` for nothing, ``None`` if atlas cannot tell.

    ``libexec/run_game.sh:369-410``: only the shipped file is read, a section
    named twice comes back as two roots xmllint refuses (so nothing), a
    ``systempath`` entry is taken through ``command -v`` and ``-x`` (``:385``),
    a ``staticpath`` entry as written through ``-x`` (``:395``) — no ``~``
    expansion, no wildcard, no ``|`` split.
    """
    if token == _RUN_GAME_SHELL[0]:
        return _RUN_GAME_SHELL[1]
    if _RUN_GAME_TOKEN.fullmatch(token) is None or token in shipped.repeated_emulators:
        return ""
    rules = shipped.emulators.get(token)
    if rules is None:
        return ""
    for entry in rules.system_paths:
        picked = _run_game_command(entry, view)
        if picked != "":
            return picked
    for entry in rules.static_paths:
        if any(mark in entry for mark in _RUN_GAME_UNREADABLE) or not entry.startswith("/"):
            continue
        probe = view.executable(entry)
        if probe == PROBE_UNKNOWN:
            return None
        if probe == PROBE_HIT:
            return entry
    return ""


def _run_game_command(entry: str, view: LaunchView) -> str | None:
    """``command -v`` then ``-x`` over one ``systempath`` entry: the path it resolves to, ``""`` for none."""
    if not entry or any(mark in entry for mark in _RUN_GAME_UNREADABLE):
        return ""
    candidates = (entry,) if "/" in entry else tuple(f"{d}/{entry}" for d in view.search_path)
    for candidate in candidates:
        if not candidate.startswith("/"):
            continue
        probe = view.executable(candidate)
        if probe == PROBE_UNKNOWN:
            return None
        if probe == PROBE_HIT:
            return candidate
    return ""


def _run_game_note(token: str, launcher: Launcher, shipped: FindRules | None, view: LaunchView) -> Caveat | None:
    """``run-game-differs`` where RetroDECK's direct start would run something other than ES-DE does, else ``None``."""
    if shipped is None:
        return None
    picked = _run_game_pick(token, shipped, view)
    if picked is None:
        return None
    if launcher.replacement_command is None and picked and generic_path(picked) == launcher.path:
        return None
    return Caveat(
        CAVEAT_RUN_GAME_DIFFERS,
        "RetroDECK's own direct start (run_game.sh) reads only the shipped find rules, never "
        "expands ~ and takes no | entry — it would run "
        + (picked or "nothing")
        + " where ES-DE runs what this entry's launcher names",
        {"run_game_path": picked},
    )
