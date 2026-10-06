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
     branch; the RetroDECK build is not ``FLATPAK_BUILD``).
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

What this module never does is touch the machine. Every probe goes through a
:class:`LaunchView`, which is the frontend's own view of the filesystem — for
RetroDECK the inside of its sandbox — and which answers *could not tell* where
that view cannot be established. A probe that cannot tell stops the walk,
because the entry after it might be the one ES-DE would take: the verdict is
then ``unestablished`` with the path that stopped it, never a guessed hit or
miss.

The cost is ``stat``, ``lstat`` and directory listings here, over find rules
the caller hands in already parsed: no core is probed, nothing is hashed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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
    CAVEAT_LAUNCH_PATH_UNESTABLISHED,
    CAVEAT_LAUNCH_RESOLUTION_UNSUPPORTED,
    CAVEAT_RUN_GAME_DIFFERS,
    LAUNCH_RULES,
    LAYER_BUNDLED,
    LAYER_CUSTOM,
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


@dataclass(frozen=True, slots=True)
class Launcher:
    """What ES-DE substitutes for an entry's ``%EMULATOR_X%`` token, and the rule entry that found it."""

    path: str
    """The file the rule found, in the frontend's own spelling — for RetroDECK a path inside its sandbox, such as
    ``/app/retrodeck/components/dolphin/component_launcher.sh``; it is what ES-DE runs unless ``replacement_command`` is
    set.

    The path ES-DE checked, normalized the way it normalizes one (doubled
    separators collapsed, a trailing one dropped) and never shell-escaped:
    ES-DE escapes it for its own command line, a client running it does not.
    For a ``systempath`` hit it is the ``PATH`` directory joined to the entry.
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
    """Which rule type found it: ``systempath`` (a search of the frontend's ``PATH``) or ``staticpath`` (a path)."""
    entry: str
    """The rule entry that matched, exactly as the find rules write it — before ``~``, ``%ESPATH%`` or a wildcard was
    expanded.
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


def _es_replace(text: str, old: str, new: str) -> str:
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


def _parent(path: str) -> str:
    """ES-DE's ``getParent``: the generic path up to its last separator (``FileSystemUtil.cpp:584-595``)."""
    generic = generic_path(path)
    offset = generic.rfind("/")
    return generic[:offset] if offset != -1 else generic


# ``getEscapedPath`` puts a backslash before each of these (``FileSystemUtil.cpp:478-506``).
_ESCAPED_CHARACTERS = "\\ '\"!$^&*(){}[]?;<>"


def _escaped(path: str) -> str:
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


def _matching_files(pattern: str, view: LaunchView) -> tuple[str, ...] | None:
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
    """
    star = pattern.find("*")
    parent = _parent(pattern)
    if star <= len(parent):
        return ()
    names = view.listing(parent)
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
class _Found:
    """One hit: the launcher, and what ES-DE put into the command for it (``emulator.first``)."""

    launcher: Launcher
    substituted: str


@dataclass(frozen=True, slots=True)
class _Stopped:
    """A probe that could not tell, at the path ES-DE would have checked next."""

    path: str


def _search(rules: EmulatorRules, view: LaunchView) -> _Found | _Stopped | None:
    """``findEmulator``'s two loops over one emulator's rules — ``None`` where nothing is found."""
    for entry in rules.system_paths:
        for directory in view.search_path:
            candidate = f"{directory}/{entry}"
            probe = view.found(candidate)
            if probe == PROBE_UNKNOWN:
                return _Stopped(candidate)
            if probe == PROBE_HIT:
                launcher = Launcher(generic_path(candidate), None, view.app_id, RULE_SYSTEMPATH, entry)
                return _Found(launcher, candidate)
    for entry in rules.static_paths:
        outcome = _static_entry(entry, view)
        if outcome is not None:
            return outcome
    return None


def _static_entry(entry: str, view: LaunchView) -> _Found | _Stopped | None:
    """One ``staticpath`` entry, expanded and checked the way ``FileData.cpp:2547-2593`` does."""
    path, pipe, replacement = entry.partition("|")
    path = expand_home_path(path, view.home)
    path = _es_replace(path, _ESPATH, view.es_path)
    if _ROMPATH in path:
        rom_directory = view.rom_directory()
        if rom_directory is None:
            return _Stopped(path)
        path = _es_replace(path, _ROMPATH, rom_directory)
    if "*" in path:
        files = _matching_files(path, view)
        if files is None:
            return _Stopped(_parent(path))
        if files:
            path = files[0]
    probe = view.found(path)
    if probe == PROBE_UNKNOWN:
        return _Stopped(path)
    if probe == PROBE_MISS:
        return None
    command = replacement if pipe else None
    launcher = Launcher(generic_path(path), command, view.app_id, RULE_STATICPATH, entry)
    return _Found(launcher, replacement if pipe else _escaped(path))


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


def _emulator_rules(token: str, rules: LayeredFindRules) -> EmulatorRules | LaunchResolution:
    """The rules ES-DE holds for *token*, or the answer that says why there are none to search."""
    if not rules.bundled_read and token not in rules.custom_names:
        return _bundled_unread()
    found = rules.merged.emulators.get(token)
    if found is None or found.empty:
        return _rules_missing(token)
    return found


def _find(token: str, rules: LayeredFindRules, view: LaunchView) -> _Found | LaunchResolution:
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
    """

    def __init__(self, rules: LayeredFindRules, view: LaunchView) -> None:
        self._rules = rules
        self._view = view
        self._found: dict[str, _Found | LaunchResolution] = {}
        self._notes: dict[str, Caveat | None] = {}

    def _find(self, token: str) -> _Found | LaunchResolution:
        if token not in self._found:
            self._found[token] = _find(token, self._rules, self._view)
        return self._found[token]

    def _run_game_note(self, token: str, launcher: Launcher) -> Caveat | None:
        if token not in self._notes:
            self._notes[token] = _run_game_note(token, launcher, self._rules.shipped, self._view)
        return self._notes[token]

    def resolve(self, command: str, *, loads_core: bool) -> LaunchResolution:
        """Whether ES-DE finds what *command* launches — the emulator, then the core it names.

        *loads_core* says the entry is a libretro one, whose verdict needs the
        core found too: a libretro command naming its core by a path rather
        than through ``%CORE_X%`` is one ES-DE never checks, so it is not
        evaluated either (``launch-resolution-unsupported``). A command
        without an ``%EMULATOR_X%`` token goes through ES-DE's second method —
        its first word, taken as a path or searched on ``PATH``
        (``FileData.cpp:2595-2649``) — and taking a command apart is not this
        evaluation's, so it answers ``launch-resolution-unsupported``. So does
        a command carrying ``%EMUPATH%`` (checked against the emulator's
        directory, ``:1399-1464``) or a malformed ``%CORE_X%``.

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
        return _with_custom_note(self._resolve(command, loads_core=loads_core), self._rules)

    def _resolve(self, command: str, *, loads_core: bool) -> LaunchResolution:
        token = _token(command, _EMULATOR_OPENER)
        if token is None:
            return command_unsupported("the command names no %EMULATOR_…% token, and taking it apart is a later step")
        if _EMUPATH in command:
            return command_unsupported("the command uses %EMUPATH%, which this evaluation does not follow")
        precommand = _token(command, _PRECOMMAND_OPENER)
        if precommand is not None:
            before = self._find(precommand)
            if isinstance(before, LaunchResolution):
                return before
        found = self._find(token)
        if isinstance(found, LaunchResolution):
            return found
        reference = _core_reference(command)
        if reference is None and loads_core:
            return command_unsupported(
                "the core is named by a path rather than through %CORE_…%, which ES-DE never checks"
            )
        core_path: str | None = None
        if reference is not None:
            core = _find_core(reference, found, self._rules, self._view)
            if isinstance(core, LaunchResolution):
                return core
            core_path = core if loads_core else None
        note = self._run_game_note(token, found.launcher)
        notes = () if note is None else (note,)
        return LaunchResolution(AVAILABILITY_STARTABLE, found.launcher, core_path, notes)


def _find_core(
    reference: _CoreReference, found: _Found, rules: LayeredFindRules, view: LaunchView
) -> str | LaunchResolution:
    """The core file the ``corepath`` rules find, or the answer that says they find none."""
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
            candidate = candidate.replace(_EMUPATH, _parent(found.substituted), 1)
        if _ESPATH in candidate:
            candidate = candidate.replace(_ESPATH, view.es_path, 1)
        probe = view.found(candidate)
        if probe == PROBE_UNKNOWN:
            return _path_unestablished(candidate)
        if probe == PROBE_HIT:
            return generic_path(candidate)
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


def _with_custom_note(answer: LaunchResolution, rules: LayeredFindRules) -> LaunchResolution:
    """*answer* with the skipped custom layer stated beside it, where one was skipped."""
    if rules.custom_unreadable is None:
        return answer
    note = Caveat(
        CAVEAT_FIND_RULES_UNREADABLE,
        f"the custom find rules at {rules.custom_unreadable} exist and could not be read or parsed — "
        "ES-DE skips such a file and reads the bundled one, and so does this answer",
        {"layer": LAYER_CUSTOM},
    )
    return LaunchResolution(answer.availability, answer.launcher, answer.core_path, (*answer.caveats, note))


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
