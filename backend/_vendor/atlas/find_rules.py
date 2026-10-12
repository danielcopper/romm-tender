"""ES-DE's find rules — where the frontend looks for what a launch command names.

``es_find_rules.xml`` is the file ES-DE reads to turn ``%EMULATOR_X%`` into a
program and ``%CORE_X%`` into a directory holding the core
(``FindRules::loadFindRules``, ``es-app/src/SystemData.cpp:43-215`` @ ES-DE
v3.4.1). Two layers, in this order:

1. the custom file ``<app-data>/custom_systems/es_find_rules.xml``, taken
   where it exists (``:46-51``), then
2. the bundled file, resolved as a program resource (``:59-61``).

The first definition of a name wins **whole**: an ``<emulator>`` or ``<core>``
whose name is already held is skipped, in the same file or the next one
(``:122-128``, ``:185-191``). Rules are not merged, so a custom definition
replaces the bundled one entirely — an empty one included. A layer that does
not parse, or carries no ``<ruleList>``, is skipped and the next one read
(``:97-110``).

Inside one ``<emulator>``, ES-DE keeps two lists in document order: the
``systempath`` entries and the ``staticpath`` entries; every other rule type is
skipped on this platform (``:143-149``), and a rule with no ``type`` is too
(``:131-136``).
Entry text is kept **as written** — pugixml does not trim it — so an entry
spread over several lines carries its line break and its indentation, and is a
path no file has. RetroDECK's bundled file at ``retrodeck-main-20260926-172324``
has one such corepath entry.

Pure text in, rules out. No I/O. Parsing goes through :mod:`atlas._xml` for
the reasons :mod:`atlas.esde` states.

The vocabulary the catalogue entry's launch answer speaks lives here too,
beside the file it is read from, so that :mod:`atlas.placement` can close the
data keys that are words (``find-rules-unreadable``'s ``layer``,
``launch-command-shell-syntax``'s ``construct``) without importing the
evaluations that build the caveats.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from . import _xml as ET

Availability = Literal["startable", "not-installed", "unestablished"]

AVAILABILITY_STARTABLE: Availability = "startable"
"""ES-DE's own lookup finds the program the entry launches, and for a libretro entry the core too."""
AVAILABILITY_NOT_INSTALLED: Availability = "not-installed"
"""The find rules exist and nothing they name is there, or the core the entry loads is missing."""
AVAILABILITY_UNESTABLISHED: Availability = "unestablished"
"""atlas cannot tell; the reason caveat on the entry says why."""

# The closed vocabulary those three make up: every word an entry's
# ``availability`` may be, and nothing else.
AVAILABILITIES = (AVAILABILITY_STARTABLE, AVAILABILITY_NOT_INSTALLED, AVAILABILITY_UNESTABLISHED)

LaunchRule = Literal["systempath", "staticpath"]

RULE_SYSTEMPATH: LaunchRule = "systempath"
"""The entry is a program name, found by searching the directories of the frontend's ``PATH``."""
RULE_STATICPATH: LaunchRule = "staticpath"
"""The entry is a path, found where it names a file (or a link) after ES-DE expanded it."""

# The two rule types ES-DE evaluates for an emulator on Linux, in the order it
# evaluates them: every systempath entry before every staticpath entry
# (``FileData::findEmulator``, ``es-app/src/FileData.cpp:2506-2593`` @ v3.4.1).
LAUNCH_RULES = (RULE_SYSTEMPATH, RULE_STATICPATH)

FindRulesLayer = Literal["bundled", "custom"]

LAYER_BUNDLED: FindRulesLayer = "bundled"
"""The find rules the frontend ships, or the copy under its app-data ``resources/`` that stands in for them — the
layer every name the custom one does not define comes from.
"""
LAYER_CUSTOM: FindRulesLayer = "custom"
"""The user's ``custom_systems/es_find_rules.xml``, whose definitions replace the bundled ones of the same name."""

# The closed vocabulary of the ``layer`` a ``find-rules-unreadable`` caveat
# names.
FIND_RULES_LAYERS = (LAYER_BUNDLED, LAYER_CUSTOM)

# Why a catalogue row's launch answer is not ``startable``, one reason per
# entry: nothing the rules name was found or the core is missing
# (``emulator-not-found`` beside the shared ``core-not-installed``, both
# ``not-installed``), or the answer cannot be told — no rules for the
# emulator or the core, an unread bundled layer, a case the evaluation does
# not cover, a path atlas cannot see the way the frontend sees it (all
# ``unestablished``). ``find-rules-unreadable`` on the custom layer is the
# exception: it is no reason but a note beside whatever verdict the bundled
# layer reached, because ES-DE skips that layer and reads on.
CAVEAT_EMULATOR_NOT_FOUND = "emulator-not-found"
CAVEAT_EMULATOR_RULES_MISSING = "emulator-rules-missing"
CAVEAT_CORE_RULES_MISSING = "core-rules-missing"
CAVEAT_FIND_RULES_UNREADABLE = "find-rules-unreadable"
CAVEAT_LAUNCH_RESOLUTION_UNSUPPORTED = "launch-resolution-unsupported"
CAVEAT_LAUNCH_PATH_UNESTABLISHED = "launch-path-unestablished"
# A note beside ``startable``: RetroDECK's own direct start would run
# something else, or nothing.
CAVEAT_RUN_GAME_DIFFERS = "run-game-differs"

# Why a launch command is not taken apart (#573), each a refusal of the whole
# command: shell syntax beyond plain words, a value a second shell would not
# read as itself, an ``env`` option, a ``.desktop`` file the command would
# parse, a ``%STARTDIR%`` or ``%INJECT%`` entry ES-DE itself refuses as
# invalid, an ``%INJECT%`` file atlas cannot read or one that injects itself
# again, a ``%ROMPATH%`` whose ROM root is not established, a command that
# reads as no words at all, a command larger than atlas follows, a label the
# catalogue does not carry for the system, and a frontend build no reading of
# ES-DE is pinned for.
CAVEAT_LAUNCH_COMMAND_SHELL_SYNTAX = "launch-command-shell-syntax"
CAVEAT_LAUNCH_COMMAND_SECOND_SHELL_UNSAFE = "launch-command-second-shell-unsafe"
CAVEAT_LAUNCH_COMMAND_ENV_OPTION = "launch-command-env-option"
CAVEAT_LAUNCH_COMMAND_DESKTOP_FILE = "launch-command-desktop-file"
CAVEAT_LAUNCH_COMMAND_ENTRY_INVALID = "launch-command-entry-invalid"
CAVEAT_LAUNCH_COMMAND_INJECT_UNREADABLE = "launch-command-inject-unreadable"
CAVEAT_LAUNCH_COMMAND_INJECT_LOOP = "launch-command-inject-loop"
CAVEAT_LAUNCH_COMMAND_ROM_ROOT_UNESTABLISHED = "launch-command-rom-root-unestablished"
CAVEAT_LAUNCH_COMMAND_NO_PROGRAM = "launch-command-no-program"
CAVEAT_LAUNCH_COMMAND_BEYOND_LIMITS = "launch-command-beyond-limits"
CAVEAT_LAUNCH_COMMAND_LABEL_UNKNOWN = "launch-command-label-unknown"
CAVEAT_FRONTEND_BUILD_UNPINNED = "frontend-build-unpinned"
# The two notes beside a command that is answered: a placeholder ES-DE leaves
# in the command as written, and a value ES-DE's own substitution would break,
# answered as the value instead.
CAVEAT_LAUNCH_COMMAND_PLACEHOLDER_UNKNOWN = "launch-command-placeholder-unknown"
CAVEAT_LAUNCH_COMMAND_ARGUMENT_BROKEN = "launch-command-argument-broken"

LaunchCommandLimit = Literal["command-length", "substitutions", "rescans", "injections", "inject-file-size"]

LIMIT_COMMAND_LENGTH: LaunchCommandLimit = "command-length"
"""The command, with everything spliced into it, would be longer than atlas reads."""
LIMIT_SUBSTITUTIONS: LaunchCommandLimit = "substitutions"
"""ES-DE would substitute more values into the command than atlas follows."""
LIMIT_RESCANS: LaunchCommandLimit = "rescans"
"""One of ES-DE's replacements would rescan the command more often than atlas follows."""
LIMIT_INJECTIONS: LaunchCommandLimit = "injections"
"""The command, with what its injections splice in, holds more ``%INJECT%`` entries than atlas follows."""
LIMIT_INJECT_FILE_SIZE: LaunchCommandLimit = "inject-file-size"
"""An ``%INJECT%`` file is larger than atlas reads to learn what ES-DE would make of it."""

# The closed vocabulary of the ``limit`` a ``launch-command-beyond-limits``
# refusal names: each bound atlas puts on taking one command apart.
LAUNCH_COMMAND_LIMITS = (
    LIMIT_COMMAND_LENGTH,
    LIMIT_SUBSTITUTIONS,
    LIMIT_RESCANS,
    LIMIT_INJECTIONS,
    LIMIT_INJECT_FILE_SIZE,
)

ShellConstruct = Literal[
    "pipe",
    "and-or",
    "list",
    "background",
    "redirection",
    "subshell",
    "expansion",
    "substitution",
    "glob",
    "brace",
    "comment",
    "tilde",
    "assignment",
    "reserved-word",
    "unterminated-quote",
    "trailing-escape",
    "nul",
]

CONSTRUCT_PIPE: ShellConstruct = "pipe"
"""An unquoted ``|``: the shell starts a second command and feeds it the first one's output."""
CONSTRUCT_AND_OR: ShellConstruct = "and-or"
"""An unquoted ``&&`` or ``||``: a second command runs depending on how the first one ends."""
CONSTRUCT_LIST: ShellConstruct = "list"
"""An unquoted ``;`` or line break: the shell runs a second command after the first."""
CONSTRUCT_BACKGROUND: ShellConstruct = "background"
"""An unquoted single ``&`` inside the command: the shell sends what precedes it to the background."""
CONSTRUCT_REDIRECTION: ShellConstruct = "redirection"
"""An unquoted ``<`` or ``>``: the shell opens a file for the command rather than passing a word."""
CONSTRUCT_SUBSHELL: ShellConstruct = "subshell"
"""An unquoted ``(`` or ``)``: a grouping or a syntax error, never a word."""
CONSTRUCT_EXPANSION: ShellConstruct = "expansion"
"""A ``$`` outside single quotes that starts a parameter, an arithmetic or a command substitution the shell
evaluates — a ``$`` before anything else is an ordinary character.
"""
CONSTRUCT_SUBSTITUTION: ShellConstruct = "substitution"
"""A backtick outside single quotes: a command the shell runs and splices its output in for."""
CONSTRUCT_GLOB: ShellConstruct = "glob"
"""An unquoted ``*``, ``?`` or ``[``: a pattern the shell matches against file names in the working folder."""
CONSTRUCT_BRACE: ShellConstruct = "brace"
"""An unquoted ``{…,…}`` or ``{…..…}``: a brace expansion the frontend's shell (bash) performs."""
CONSTRUCT_COMMENT: ShellConstruct = "comment"
"""An unquoted ``#`` that starts a word: the shell drops it and the rest of the line."""
CONSTRUCT_TILDE: ShellConstruct = "tilde"
"""An unquoted ``~`` that starts a word: the shell replaces it with a home folder of its own choosing."""
CONSTRUCT_ASSIGNMENT: ShellConstruct = "assignment"
"""A first word of the form ``NAME=value``: the shell sets a variable rather than running a program."""
CONSTRUCT_RESERVED_WORD: ShellConstruct = "reserved-word"
"""A first word the shell reads as its own grammar (``if``, ``!``, ``time`` …) rather than a program."""
CONSTRUCT_UNTERMINATED_QUOTE: ShellConstruct = "unterminated-quote"
"""A quote that never closes, so the shell reads ES-DE's own suffix into it."""
CONSTRUCT_TRAILING_ESCAPE: ShellConstruct = "trailing-escape"
"""A backslash that ends the command, so the shell escapes the space ES-DE appends after it."""
CONSTRUCT_NUL: ShellConstruct = "nul"
"""A NUL character: the C string ES-DE hands the shell ends there, and the rest of the command never reaches it."""

# The closed vocabulary of the ``construct`` a ``launch-command-shell-syntax``
# refusal names: every reading of the command that is more than a plain word.
SHELL_CONSTRUCTS = (
    CONSTRUCT_PIPE,
    CONSTRUCT_AND_OR,
    CONSTRUCT_LIST,
    CONSTRUCT_BACKGROUND,
    CONSTRUCT_REDIRECTION,
    CONSTRUCT_SUBSHELL,
    CONSTRUCT_EXPANSION,
    CONSTRUCT_SUBSTITUTION,
    CONSTRUCT_GLOB,
    CONSTRUCT_BRACE,
    CONSTRUCT_COMMENT,
    CONSTRUCT_TILDE,
    CONSTRUCT_ASSIGNMENT,
    CONSTRUCT_RESERVED_WORD,
    CONSTRUCT_UNTERMINATED_QUOTE,
    CONSTRUCT_TRAILING_ESCAPE,
    CONSTRUCT_NUL,
)


@dataclass(frozen=True, slots=True)
class EmulatorRules:
    """One ``<emulator>``'s two entry lists, each as written and in document order."""

    system_paths: tuple[str, ...] = ()
    static_paths: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        """No entry of either type — what ES-DE answers ``NO_RULES`` for (``FileData.cpp:2353-2355``)."""
        return not self.system_paths and not self.static_paths


@dataclass(frozen=True, slots=True)
class FindRules:
    """One find-rules layer, or the merge of both: rules by emulator name and core name.

    ``repeated_emulators`` names the ``<emulator>`` names one file defines more
    than once. ES-DE keeps the first and skips the rest, so it changes nothing
    for the frontend; it is kept for RetroDECK's ``run_game.sh``, whose XPath
    read of such a name comes back as two sections it cannot parse.
    """

    emulators: Mapping[str, EmulatorRules]
    cores: Mapping[str, tuple[str, ...]]
    repeated_emulators: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "emulators", MappingProxyType(dict(self.emulators)))
        object.__setattr__(self, "cores", MappingProxyType(dict(self.cores)))


NO_FIND_RULES = FindRules(emulators={}, cores={})


def parse_find_rules(text: str) -> FindRules | None:
    """One ``es_find_rules.xml`` as ES-DE reads it, or ``None`` where ES-DE skips the file.

    ``None`` is both of the frontend's skips: the text does not parse, or no
    document-level ``<ruleList>`` is there (``SystemData.cpp:97-110``). The
    document is read at the document level the way :func:`atlas.esde.parse_es_systems`
    reads ``es_systems.xml`` — wrapped in a synthetic root, BOM and XML
    declaration off first — because pugixml's ``doc.child`` is what ES-DE asks,
    and the first ``<ruleList>`` is the one it gets.

    Within the file the first ``<emulator>`` (or ``<core>``) of a name wins and
    a later one is skipped, as is one with an empty ``name``
    (``:115-128``, ``:179-191``).
    """
    rule_list = _rule_list(text)
    if rule_list is None:
        return None
    emulators: dict[str, EmulatorRules] = {}
    repeated: set[str] = set()
    for emulator_el in rule_list.findall("emulator"):
        name = emulator_el.get("name") or ""
        if name in emulators:
            repeated.add(name)
        elif name:
            emulators[name] = _emulator_rules(emulator_el)
    cores: dict[str, tuple[str, ...]] = {}
    for core_el in rule_list.findall("core"):
        name = core_el.get("name") or ""
        if name and name not in cores:
            cores[name] = _core_paths(core_el)
    return FindRules(emulators=emulators, cores=cores, repeated_emulators=frozenset(repeated))


def _rule_list(text: str) -> ET.Element | None:
    """The document's first ``<ruleList>``, read at the document level — ``None`` where there is none to read."""
    stripped = text.removeprefix("\ufeff").strip()
    if stripped.startswith("<?"):
        end = stripped.find("?>")
        if end != -1:
            stripped = stripped[end + 2 :]
    try:
        root = ET.fromstring(f"<atlas-wrapper>{stripped}</atlas-wrapper>")
    except ET.ParseError:
        return None
    return root.find("ruleList")


def _core_paths(core_el: ET.Element) -> tuple[str, ...]:
    """One ``<core>``'s ``corepath`` entries as written, every other rule type dropped (``SystemData.cpp:192-210``)."""
    return tuple(
        entry.text or ""
        for rule in core_el.findall("rule")
        if rule.get("type") == "corepath"
        for entry in rule.findall("entry")
    )


def _emulator_rules(emulator_el: ET.Element) -> EmulatorRules:
    """The two lists of one ``<emulator>``, the other rule types dropped as ES-DE drops them on Linux."""
    system_paths: list[str] = []
    static_paths: list[str] = []
    for rule in emulator_el.findall("rule"):
        kind = rule.get("type")
        if kind == RULE_SYSTEMPATH:
            system_paths.extend(entry.text or "" for entry in rule.findall("entry"))
        elif kind == RULE_STATICPATH:
            static_paths.extend(entry.text or "" for entry in rule.findall("entry"))
    return EmulatorRules(tuple(system_paths), tuple(static_paths))


def merge_find_rules(layers: tuple[FindRules, ...]) -> FindRules:
    """*layers* in ES-DE's load order merged its way: the first definition of a name wins whole."""
    emulators: dict[str, EmulatorRules] = {}
    cores: dict[str, tuple[str, ...]] = {}
    for layer in layers:
        for name, rules in layer.emulators.items():
            emulators.setdefault(name, rules)
        for name, paths in layer.cores.items():
            cores.setdefault(name, paths)
    return FindRules(emulators=emulators, cores=cores)
