"""The command ES-DE would run for one file, taken apart: program, arguments, environment, working folder.

ES-DE builds a launch command as one string and hands it to a shell
(``FileData::launchGame``, ``es-app/src/FileData.cpp:953-2105`` @ ES-DE v3.4.1;
RetroDECK's fork at :data:`atlas.installations.RetroDeck.ESDE_FORK_BUILD`
carries the Linux path of that function, ``findEmulator`` and the
``FileSystemUtil`` helpers below unchanged — it differs in Android blocks, a
playtime guard and a ``Path=`` read for ``.desktop`` files, which are refused
here). This module mirrors that string's assembly and then reads it the way the
shell does, so a client can start the program itself:

1. **The values** ES-DE computes from the file first (``:1018-1045``):
   ``%ROM%`` is the path through ``getEscapedPath``, ``%BASENAME%`` the stem
   (``getStem``, which strips nothing from a directory), ``%ROMRAW%`` the path
   as it is, ``%FILENAME%`` the stem and the extension ``getExtension`` reads
   off the *escaped* path. A directory ES-DE takes as a file is launched
   through a file of its own name inside it, where one is (``:1030-1043``).
2. **The edits**, in ES-DE's order: ``%RUNINBACKGROUND%`` and
   ``%ENABLESHORTCUTS%`` removed with the leading blanks (``:1057-1093``), the
   quote before ``%CORE_`` dropped (``:1145-1151``), every ``~`` replaced with
   ES-DE's home (``:1164``), the ``%PRECOMMAND_X%`` and ``%EMULATOR_X%`` tokens
   replaced with what the find rules found (``:1166-1294``, ``:2536-2590``),
   the ``%CORE_X%/name`` reference with the core file (``:1484-1551``),
   ``%STARTDIR%`` cut out (``:1570-1670``), each ``%INJECT%`` replaced with the
   text of its file (``:1672-1791``), then ``%ROM%`` … ``%ROMPATH%``
   (``:1899-1907``) and ``%ESPATH%`` … ``%GAMEDIRRAW%`` (``:2092-2101``), and
   the result trimmed (``:2105``). Every placeholder replaced by name goes
   through ``Utils::String::replace`` (:func:`atlas.launch.es_replace`), which
   rescans until none is left.
3. **The shell** (``launchGameUnix``, ``es-core/src/utils/PlatformUtil.cpp:162-191``)
   runs ``cd <folder> && <command> 2>&1 &``. The frontend's ``/bin/sh`` is
   bash. The command is read here by POSIX token rules into words; anything
   beyond plain words is refused (``launch-command-shell-syntax``). A leading
   ``env`` with its ``NAME=value`` words becomes the environment.

Every character of the string carries where it came from: the command as
written, an injected file, or a value ES-DE substituted. That is what tells a
command that is more than plain words (refused) from a *value* ES-DE's own
escaping does not carry through the shell — ``getEscapedPath`` leaves ``|``,
backticks, tabs and line breaks alone and turns a ``\\`` into a ``/``, and
``%ROMRAW%`` is not escaped at all. Such an argument is answered as the value
the substitution meant, with ``launch-command-argument-broken`` beside it.

An escaped value inside quotes keeps ES-DE's backslashes, because they are
there for a second shell the argument is handed to (``sh -c "%ROM%"``). That
shell is read too: the text the first one yields for the value has to read as
exactly one plain word equal to the value, or the whole command is refused
(``launch-command-second-shell-unsafe``) — an argument atlas re-quoted would
be a command ES-DE never builds, and one it passed on would run what the file
name spells. Where a shell is evident — the command string a shell's ``-c``
reads, found by walking its options the way it does — that string is read
again with every value in it, quoted or not, escaped or raw, together with the
command's own text around it, and each value must come out literal. A raw
value inside quotes anywhere else carries no sign of a second shell, so it
keeps the first shell's reading alone: a wrapper that evaluates its argument
in a shell of its own is a case this does not see.

What the module never does is touch the machine: every probe goes through a
:class:`CommandView`, the frontend's own view of the filesystem.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Callable, Literal, Protocol

from .esde import expand_home_path
from .find_rules import (
    CAVEAT_LAUNCH_COMMAND_ARGUMENT_BROKEN,
    CAVEAT_LAUNCH_COMMAND_BEYOND_LIMITS,
    CAVEAT_LAUNCH_COMMAND_DESKTOP_FILE,
    CAVEAT_LAUNCH_COMMAND_ENTRY_INVALID,
    CAVEAT_LAUNCH_COMMAND_ENV_OPTION,
    CAVEAT_LAUNCH_COMMAND_INJECT_LOOP,
    CAVEAT_LAUNCH_COMMAND_INJECT_UNREADABLE,
    CAVEAT_LAUNCH_COMMAND_NO_PROGRAM,
    CAVEAT_LAUNCH_COMMAND_PLACEHOLDER_UNKNOWN,
    CAVEAT_LAUNCH_COMMAND_ROM_ROOT_UNESTABLISHED,
    CAVEAT_LAUNCH_COMMAND_SECOND_SHELL_UNSAFE,
    CAVEAT_LAUNCH_COMMAND_SHELL_SYNTAX,
    CAVEAT_LAUNCH_PATH_UNESTABLISHED,
    CONSTRUCT_AND_OR,
    CONSTRUCT_ASSIGNMENT,
    CONSTRUCT_BACKGROUND,
    CONSTRUCT_BRACE,
    CONSTRUCT_COMMENT,
    CONSTRUCT_EXPANSION,
    CONSTRUCT_GLOB,
    CONSTRUCT_LIST,
    CONSTRUCT_NUL,
    CONSTRUCT_PIPE,
    CONSTRUCT_REDIRECTION,
    CONSTRUCT_RESERVED_WORD,
    CONSTRUCT_SUBSHELL,
    CONSTRUCT_SUBSTITUTION,
    CONSTRUCT_TILDE,
    CONSTRUCT_TRAILING_ESCAPE,
    CONSTRUCT_UNTERMINATED_QUOTE,
    LIMIT_COMMAND_LENGTH,
    LIMIT_INJECT_FILE_SIZE,
    LIMIT_INJECTIONS,
    LIMIT_RESCANS,
    LIMIT_SUBSTITUTIONS,
    RULE_SYSTEMPATH,
    LaunchCommandLimit,
)
from .launch import (
    PROBE_HIT,
    PROBE_UNKNOWN,
    CommandParts,
    Found,
    LaunchView,
    Probe,
    es_replace,
    escaped_path,
    generic_path,
    parent_path,
)
from .machine import READ_MISSING, ReadResult
from .placement import Caveat

ProgramVariant = Literal["flatpak", "appimage", "native"]

VARIANT_FLATPAK: ProgramVariant = "flatpak"
"""A command run inside a Flatpak app's sandbox: ``app_id`` and the ``command`` inside it."""
VARIANT_APPIMAGE: ProgramVariant = "appimage"
"""An AppImage the host runs: its ``path``."""
VARIANT_NATIVE: ProgramVariant = "native"
"""A program the host runs as it is: its ``path``."""

# The closed vocabulary of a launch program's ``variant``.
PROGRAM_VARIANTS = (VARIANT_FLATPAK, VARIANT_APPIMAGE, VARIANT_NATIVE)

InjectionOutcome = Literal["injected", "absent", "empty", "oversized"]

INJECTION_INJECTED: InjectionOutcome = "injected"
"""The file was read and its text spliced into the command."""
INJECTION_ABSENT: InjectionOutcome = "absent"
"""No file or link is there, so ES-DE injects nothing."""
INJECTION_EMPTY: InjectionOutcome = "empty"
"""The file is there and yields no text — empty, or a link that leads nowhere — so ES-DE injects nothing."""
INJECTION_OVERSIZED: InjectionOutcome = "oversized"
"""The file's text is longer than ES-DE's limit of 4096 bytes, so ES-DE skips it and injects nothing."""

# The closed vocabulary of what one ``%INJECT%`` did.
INJECTION_OUTCOMES = (INJECTION_INJECTED, INJECTION_ABSENT, INJECTION_EMPTY, INJECTION_OVERSIZED)

# ES-DE stops reading an injection file past this many bytes and skips it
# (``FileData.cpp:1761-1778``).
_INJECT_LIMIT = 4096

# What atlas follows of one command before it refuses it
# (``launch-command-beyond-limits``, naming the bound). ES-DE bounds only the
# 4096 bytes one injection may splice in: neither the command's length, nor
# how many values it substitutes, nor how many passes one replacement makes,
# nor how many %INJECT% entries it resolves — and it reads an injection file
# line by line to its end where the lines carry no text. These bounds keep
# every reading of the command linear in a length that is itself bounded, and
# no deployed command comes near any of them.
COMMAND_LENGTH_LIMIT = 16384
"""Characters the command may hold once everything is spliced in."""
SUBSTITUTION_LIMIT = 256
"""Values ES-DE may substitute into one command."""
RESCAN_LIMIT = 64
"""Passes one replacement may make over the command."""
INJECTION_LIMIT = 16
"""``%INJECT%`` entries one command may resolve, those the injected text brings included."""
INJECT_FILE_SIZE_LIMIT = 1 << 20
"""Bytes an injection file may hold for atlas to read it; a larger one is refused unread."""


@dataclass(frozen=True, slots=True)
class LaunchProgram:
    """What the command starts, tagged by how it is started — the keys a variant does not use are ``None``."""

    variant: ProgramVariant
    """How the program is started: one of :data:`PROGRAM_VARIANTS`."""
    app_id: str | None = None
    """The Flatpak app whose sandbox the program runs in — ``flatpak`` only, ``null`` on the other variants."""
    command: str | None = None
    """The program inside that sandbox, as the shell inside it would run it (``flatpak run --command=…``) —
    ``flatpak`` only, ``null`` on the other variants.

    A path where the find rules found one; a bare name where the command
    names its program that way, which the sandbox's ``PATH`` resolves.
    """
    path: str | None = None
    """The file the host runs — ``appimage`` and ``native`` only, ``null`` on ``flatpak``."""

    def __post_init__(self) -> None:
        if self.variant not in PROGRAM_VARIANTS:
            raise ValueError(f"LaunchProgram: variant must be one of {PROGRAM_VARIANTS}, got {self.variant!r}")
        sandboxed = (self.app_id is not None, self.command is not None, self.path is None)
        if sandboxed != ((True,) * 3 if self.variant == VARIANT_FLATPAK else (False, False, False)):
            raise ValueError(
                "LaunchProgram: a flatpak program carries app_id and command and no path; "
                "an appimage or native one a path alone"
            )


@dataclass(frozen=True, slots=True)
class EnvironmentVariable:
    """One variable the command sets for the program, in the order the command sets them."""

    name: str
    """The variable's name, as the command writes it."""
    value: str
    """Its value, as the shell hands it over — quotes and escapes already read."""


@dataclass(frozen=True, slots=True)
class WorkingFolder:
    """The folder the frontend changes into before it runs the command (``%STARTDIR%``)."""

    frontend_path: str
    """The folder as the frontend spells it — inside its sandbox for RetroDECK, with ``~`` read as its ``--home``."""
    host_path: str | None
    """The same folder as the host spells it; ``null`` where atlas cannot place it, and a
    ``launch-path-unestablished`` caveat then says which path that is.
    """
    created_if_missing: bool
    """Whether the frontend creates the folder when it is not there — ES-DE does (``FileData.cpp:1641-1643``)."""


@dataclass(frozen=True, slots=True)
class Injection:
    """One ``%INJECT%`` of the command: the file ES-DE reads, and what came of it."""

    file: str
    """The file ES-DE reads, as the frontend spells it."""
    outcome: InjectionOutcome
    """What ES-DE did with it: one of :data:`INJECTION_OUTCOMES`."""


@dataclass(frozen=True, slots=True)
class LaunchCommand:
    """The command the frontend would run for one file, taken apart."""

    program: LaunchProgram
    """What the command starts."""
    arguments: tuple[str, ...]
    """The arguments the program receives, in order and in the frontend's spelling, every placeholder resolved."""
    environment: tuple[EnvironmentVariable, ...]
    """The variables a leading ``env`` sets for the program, in its order — empty where the command sets none."""
    working_folder: WorkingFolder | None
    """The folder the frontend runs the command in, ``null`` where the command names none."""
    injections: tuple[Injection, ...]
    """Every ``%INJECT%`` ES-DE resolved, in the order it resolved them — empty where the command has none."""


TooLarge = Literal["too-large"]
TOO_LARGE: TooLarge = "too-large"


class CommandView(LaunchView, Protocol):
    """The frontend's view of the filesystem, with the three reads taking a command apart needs beyond the lookup."""

    def is_directory(self, path: str) -> Probe:
        """ES-DE's ``isDirectory`` on *path*, links followed."""
        ...

    def host_path(self, path: str) -> str | None:
        """Where the host reads the frontend's *path*, links followed — ``None`` where that cannot be told."""
        ...

    def read_text_within(self, path: str, limit: int) -> ReadResult | TooLarge | None:
        """The text at the frontend's *path*, links followed, read no further than *limit* + 1 bytes —
        :data:`TOO_LARGE` where the file holds more than *limit*, ``None`` where what is there cannot be told.
        """
        ...


@dataclass(frozen=True, slots=True)
class TakenApart:
    """One command's outcome: the command and the notes beside it, or no command and the one reason why."""

    command: LaunchCommand | None
    caveats: tuple[Caveat, ...] = ()


# --- the two spellings of a path -------------------------------------------
#
# Every value is computed twice: as ES-DE computes it, and as it means it.
# The two differ only where ES-DE's own helpers lose something — its
# getGenericPath turns a "\\" in a file name into a "/", and getExtension reads
# the escaped path — and the second is what an argument ES-DE breaks is
# answered with.


def _kept_generic(path: str) -> str:
    """``getGenericPath`` without its one lossy step: doubled separators collapsed, a trailing one dropped."""
    while "//" in path:
        path = path.replace("//", "/")
    while len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


# The characters getEscapedPath puts a backslash before (FileSystemUtil.cpp:478-506).
_SHELL_ESCAPED = "\\ '\"!$^&*(){}[]?;<>"


def _meant_escape(path: str) -> str:
    """What escaping *path* for the shell means: a backslash before every character ``getEscapedPath`` escapes."""
    return "".join("\\" + char if char in _SHELL_ESCAPED else char for char in _kept_generic(path))


def _file_name(path: str, *, kept: bool) -> str:
    """``getFileName`` (``FileSystemUtil.cpp:597-609``)."""
    generic = _kept_generic(path) if kept else generic_path(path)
    offset = generic.rfind("/")
    if offset == -1:
        return generic
    return generic[offset + 1 :] or "."


def _stem(path: str, *, is_directory: bool, kept: bool) -> str:
    """``getStem`` (``FileSystemUtil.cpp:611-628``): the name up to its last dot, where *path* is no directory."""
    name = _file_name(path, kept=kept)
    if name == "." or is_directory:
        return name
    offset = name.rfind(".")
    return name[:offset] if offset != -1 else name


def _extension(path: str, *, kept: bool) -> str:
    """``getExtension`` (``FileSystemUtil.cpp:630-645``): from the last dot, ``.`` where there is none."""
    name = _file_name(path, kept=kept)
    if name == ".":
        return name
    offset = name.rfind(".")
    return name[offset:] if offset != -1 else "."


def _kept_parent(path: str) -> str:
    """``getParent`` over the kept spelling."""
    generic = _kept_generic(path)
    offset = generic.rfind("/")
    return generic[:offset] if offset != -1 else generic


@dataclass(frozen=True, slots=True)
class _Value:
    """One value ES-DE substitutes: what it puts in, and what that means.

    ``meant`` is the value itself; ``meant_text`` what a correct substitution
    would put in its place — escaped where ES-DE escapes this value, the value
    itself where it does not.
    """

    placeholder: str
    actual: str
    meant: str
    escaped: bool

    @property
    def meant_text(self) -> str:
        return _meant_escape(self.meant) if self.escaped else self.meant


def _escaped_value(placeholder: str, actual_path: str, meant_path: str) -> _Value:
    """A path ES-DE puts in through ``getEscapedPath``."""
    return _Value(placeholder, escaped_path(actual_path), _kept_generic(meant_path), escaped=True)


def _raw_value(placeholder: str, actual: str, meant: str) -> _Value:
    """A value ES-DE puts in as it is."""
    return _Value(placeholder, actual, meant, escaped=False)


# --- the string, with where each character came from ------------------------

SegmentKind = Literal["command", "inject", "value"]
_COMMAND = 0


@dataclass(slots=True)
class _Segment:
    """Where a run of the string came from: the command as written (and a ``|`` replacement), an injected file, or a
    substituted value.
    """

    kind: SegmentKind
    value: _Value | None = None
    file: str = ""
    parent: int = -1
    mutated: bool = False


class _Built:
    """The command string as ES-DE edits it, each character tagged with the segment it came from."""

    def __init__(self, command: str) -> None:
        self.text = command
        self.origins = [_COMMAND] * len(command)
        self.segments = [_Segment("command")]
        self.substitutions = 0
        self._check_length()

    def _check_length(self) -> None:
        if len(self.text) > COMMAND_LENGTH_LIMIT:
            raise _Refused(_beyond_limit(LIMIT_COMMAND_LENGTH))

    def find(self, needle: str, start: int = 0) -> int:
        return self.text.find(needle, start)

    def byte_size(self) -> int:
        """``std::string::size()``: ES-DE measures in bytes."""
        return len(self.text.encode("utf-8"))

    def byte_offset(self, index: int) -> int:
        return len(self.text[:index].encode("utf-8"))

    def splice(self, start: int, end: int, insert: str = "", origin: int = _COMMAND) -> None:
        self.text = self.text[:start] + insert + self.text[end:]
        self.origins[start:end] = [origin] * len(insert)
        self._check_length()

    def add(self, segment: _Segment) -> int:
        if segment.kind == "value":
            self.substitutions += 1
            if self.substitutions > SUBSTITUTION_LIMIT:
                raise _Refused(_beyond_limit(LIMIT_SUBSTITUTIONS))
        self.segments.append(segment)
        return len(self.segments) - 1

    def is_value(self, origin: int) -> bool:
        return self.segments[origin].kind == "value"

    def _mark_mutated(self, start: int, end: int) -> None:
        for origin in self.origins[start:end]:
            if self.is_value(origin):
                self.segments[origin].mutated = True

    def replace(self, old: str, value: _Value | None) -> None:
        """``Utils::String::replace(command, old, …)`` (``StringUtil.cpp:267-297``): every *old* replaced, rescanned
        until none is left, once only where the new text holds *old* itself.

        *value* is what goes in — a new segment per occurrence — and ``None``
        removes *old*. A replacement that lands inside a value marks that value
        as one ES-DE altered. A value ES-DE substitutes as nothing where it
        stands for something (a ``\\`` in a file name can leave ``getStem``
        with nothing) is held in place by one stand-in character and marked
        altered, so the reading answers it as the value it stands for.
        """
        if value is None:
            self._rescanning(old, "", lambda: _COMMAND)
            return
        lost = not value.actual and bool(value.meant)
        new = _LOST if lost else value.actual
        self._rescanning(old, new, lambda: self.add(_Segment("value", value, mutated=lost)))

    def replace_command_text(self, old: str, new: str) -> None:
        """A replacement of the command's own text by command text — the quote ES-DE drops before ``%CORE_``."""
        self._rescanning(old, new, lambda: _COMMAND)

    def _rescanning(self, old: str, new: str, origin_of: Callable[[], int]) -> None:
        """One ``Utils::String::replace``: passes over the whole string until *old* is gone, each pass left to right
        over the string as it was, every occurrence getting the origin *origin_of* hands out.

        Each pass is one walk of the string. ES-DE bounds neither their number
        nor the string's length; atlas stops at :data:`RESCAN_LIMIT` passes.
        """
        if old == new:
            return
        passes = 0
        while old in self.text:
            passes += 1
            if passes > RESCAN_LIMIT:
                raise _Refused(_beyond_limit(LIMIT_RESCANS))
            text, origins = self.text, self.origins
            pieces: list[str] = []
            tagged: list[int] = []
            last = 0
            start = text.find(old)
            while start != -1:
                end = start + len(old)
                self._mark_mutated(start, end)
                pieces.extend((text[last:start], new))
                tagged.extend(origins[last:start])
                tagged.extend([origin_of()] * len(new))
                last = end
                start = text.find(old, end)
            pieces.append(text[last:])
            tagged.extend(origins[last:])
            self.text, self.origins = "".join(pieces), tagged
            self._check_length()
            if old in new:
                break

    def strip_leading(self) -> None:
        """The leading-blank trim ES-DE runs after removing a flag (``std::isspace``)."""
        kept = len(self.text) - len(self.text.lstrip(_ISSPACE))
        self.splice(0, kept)

    def strip(self) -> None:
        """``Utils::String::trim`` (``StringUtil.cpp:249-265``)."""
        self.strip_leading()
        kept = len(self.text.rstrip(_ISSPACE))
        self.splice(kept, len(self.text))


_ISSPACE = " \t\n\v\f\r"
# What stands in for a value ES-DE substitutes as nothing: one character from
# the other private-use plane, apart from the reading's own stand-ins.
_LOST = "\U00100000"


# --- reading the string as the shell does -----------------------------------

_DOUBLE_QUOTED_ESCAPES = '$`"\\\n'
_OPERATORS = {
    "|": CONSTRUCT_PIPE,
    "&": CONSTRUCT_BACKGROUND,
    ";": CONSTRUCT_LIST,
    "<": CONSTRUCT_REDIRECTION,
    ">": CONSTRUCT_REDIRECTION,
    "(": CONSTRUCT_SUBSHELL,
    ")": CONSTRUCT_SUBSHELL,
}
_LITERAL_EVENTS = {
    "`": CONSTRUCT_SUBSTITUTION,
    "*": CONSTRUCT_GLOB,
    "?": CONSTRUCT_GLOB,
    "[": CONSTRUCT_GLOB,
}
# What bash expands after a ``$``: a parameter by name, number or special
# character, ``${…}``, ``$(…)``, ``$[…]`` — and, outside double quotes, ``$'…'``
# and ``$"…"`` (bash(1), EXPANSION, QUOTING). A ``$`` before anything else is
# an ordinary character.
_EXPANDS_AFTER_DOLLAR = frozenset("{([@*#?-$!_0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_EXPANDS_AFTER_DOLLAR_UNQUOTED = frozenset("'\"")
# The words bash reads as grammar where a command starts: POSIX's reserved
# words and bash's own (bash(1), RESERVED WORDS).
_RESERVED = frozenset(
    "! case coproc do done elif else esac fi for function if in select then time until while { } [[ ]]".split()
)
# A shell variable name is ASCII: ``re.ASCII`` keeps ``\w`` to ``[A-Za-z0-9_]``.
_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=", re.ASCII)
# The reserved words after which the shell expects a command again.
_OPENING_RESERVED = frozenset({"if", "then", "else", "elif", "while", "until", "do", "!", "time", "{"})
# The operators after which the shell expects a command again.
_COMMAND_SEPARATORS = frozenset({CONSTRUCT_PIPE, CONSTRUCT_AND_OR, CONSTRUCT_LIST, CONSTRUCT_BACKGROUND})
# What a value event is, beside the constructs a command is refused for: a
# value that splits a word or opens or closes a quote.
_VALUE_SPLIT = "split"
_VALUE_QUOTE = "quote"
Context = Literal["plain", "double", "single"]


@dataclass(slots=True)
class _Char:
    char: str
    origin: int
    quoted: bool


@dataclass(slots=True)
class _Lexed:
    """The words the shell reads, and every reading that is more than a plain word, by the segment it came from."""

    words: list[list[_Char]] = field(default_factory=list)
    events: list[tuple[int, str]] = field(default_factory=list)
    contexts: dict[int, Context] = field(default_factory=dict)
    # Beside each event, the word it stands in: the word in progress when it
    # was read, or the next one where none was — and for an operator, which
    # stands between words rather than in one, the span of text it takes.
    event_words: list[int] = field(default_factory=list)
    operator_spans: list[tuple[int, int] | None] = field(default_factory=list)
    # The words that stand where the shell expects a command: the first, any
    # after an assignment there, and the first after ``;``, ``&``, ``|``,
    # ``&&``, ``||``, ``(`` or a line break.
    command_words: list[int] = field(default_factory=list)

    def add(self, origin: int, construct: str, word: int, span: tuple[int, int] | None = None) -> None:
        self.events.append((origin, construct))
        self.event_words.append(word)
        self.operator_spans.append(span)


class _Lexer:
    """POSIX token recognition over one tagged string (XCU 2.3, quoting 2.2), down to plain words."""

    def __init__(self, text: str, origins: list[int]) -> None:
        self.text = text
        self.origins = origins
        self.out = _Lexed()
        self.word: list[_Char] | None = None
        self.index = 0
        self.at_command = True
        self.word_at_command = False

    def _event(self, origin: int, construct: str) -> None:
        self.out.add(origin, construct, len(self.out.words))

    def _context(self, origin: int, context: Context) -> None:
        self.out.contexts.setdefault(origin, context)

    def _start_word(self) -> list[_Char]:
        if self.word is None:
            self.word = []
            self.word_at_command = self.at_command
        return self.word

    def _emit(self, char: str, origin: int, *, quoted: bool) -> None:
        self._start_word().append(_Char(char, origin, quoted))

    def _end_word(self) -> None:
        if self.word is not None:
            if self.word_at_command:
                self.out.command_words.append(len(self.out.words))
                # An assignment before the command, or a reserved word that
                # opens a command list (``then``, ``!``, ``{`` …), leaves the
                # next word in command position.
                spelled = "".join(char.char for char in self.word)
                opens = spelled in _OPENING_RESERVED and not any(char.quoted for char in self.word)
                self.at_command = opens or _assignment_end(self.word) is not None
            self.out.words.append(self.word)
            self.word = None

    def run(self) -> _Lexed:
        quote: str | None = None
        opened_by = _COMMAND
        while self.index < len(self.text):
            if quote is None:
                opened = self._plain()
                if opened is not None:
                    quote, opened_by = opened
            elif quote == "'":
                quote = self._single()
            else:
                quote = self._double()
        if quote is not None:
            self._event(opened_by, CONSTRUCT_UNTERMINATED_QUOTE)
        self._end_word()
        return self.out

    def _plain(self) -> tuple[str, int] | None:
        """One step outside quotes — the quote it opens, if it opens one."""
        char, origin = self.text[self.index], self.origins[self.index]
        self._context(origin, "plain")
        starts_word = self.word is None
        if char in " \t":
            self._event(origin, _VALUE_SPLIT)
            self._end_word()
            self.index += 1
            return None
        if char == "\n":
            self._event(origin, CONSTRUCT_LIST)
            self._end_word()
            self.at_command = True
            self.index += 1
            return None
        if char == "\\":
            self._escape(origin)
            return None
        if char in "'\"":
            self._event(origin, _VALUE_QUOTE)
            self._start_word()
            self.index += 1
            return char, origin
        if char in _OPERATORS:
            self._operator(char, origin)
            return None
        if char in _LITERAL_EVENTS:
            self._event(origin, _LITERAL_EVENTS[char])
        elif char == "$" and self._expands(quoted=False):
            self._event(origin, CONSTRUCT_EXPANSION)
        elif starts_word and char == "#":
            self._event(origin, CONSTRUCT_COMMENT)
        elif starts_word and char == "~":
            self._event(origin, CONSTRUCT_TILDE)
        self._emit(char, origin, quoted=False)
        self.index += 1
        return None

    def _expands(self, *, quoted: bool) -> bool:
        """Whether the ``$`` at the current index starts an expansion in bash.

        A backslash-newline between them is a line continuation the shell
        removes first, in and out of double quotes, so it is looked past.
        """
        after = self.index + 1
        while self.text[after : after + 2] == "\\\n":
            after += 2
        following = self.text[after : after + 1]
        if not following:
            return False
        if following in _EXPANDS_AFTER_DOLLAR:
            return True
        return not quoted and following in _EXPANDS_AFTER_DOLLAR_UNQUOTED

    def _escape(self, origin: int) -> None:
        if self.index + 1 >= len(self.text):
            self._event(origin, CONSTRUCT_TRAILING_ESCAPE)
            self.index += 1
            return
        escaped = self.text[self.index + 1]
        self.index += 2
        if escaped != "\n":
            self._emit(escaped, self.origins[self.index - 1], quoted=True)

    def _operator(self, char: str, origin: int) -> None:
        construct = _OPERATORS[char]
        doubled = self.text[self.index + 1 : self.index + 2] == char
        if char in "|&" and doubled:
            construct = CONSTRUCT_AND_OR
        width = 2 if doubled else 1
        self.out.add(origin, construct, len(self.out.words), (self.index, self.index + width))
        self._end_word()
        # After ``)`` a command may follow too — a ``case`` pattern's — so it
        # turns command position on, which reads a word after a subshell as a
        # command where the shell would not, never the other way round.
        if construct in _COMMAND_SEPARATORS or char in "()":
            self.at_command = True
        self.index += width

    def _single(self) -> str | None:
        char, origin = self.text[self.index], self.origins[self.index]
        self._context(origin, "single")
        self.index += 1
        if char == "'":
            self._event(origin, _VALUE_QUOTE)
            return None
        self._emit(char, origin, quoted=True)
        return "'"

    def _double(self) -> str | None:
        char, origin = self.text[self.index], self.origins[self.index]
        self._context(origin, "double")
        if char == '"':
            self._event(origin, _VALUE_QUOTE)
            self.index += 1
            return None
        following = self.text[self.index + 1 : self.index + 2]
        if char == "\\" and following and following in _DOUBLE_QUOTED_ESCAPES:
            self.index += 2
            if following != "\n":
                self._emit(following, self.origins[self.index - 1], quoted=True)
            return '"'
        if char == "`":
            self._event(origin, CONSTRUCT_SUBSTITUTION)
        elif char == "$" and self._expands(quoted=True):
            self._event(origin, CONSTRUCT_EXPANSION)
        self._emit(char, origin, quoted=True)
        self.index += 1
        return '"'


def _brace_expansion(word: list[_Char]) -> int | None:
    """The origin of an unquoted ``{`` that may open a brace expansion bash performs in *word* — ``None`` for none.

    bash expands ``{a,b}`` and ``{a..b}``: an unquoted ``{``, not after a
    ``$``, with an unquoted ``,`` or ``..`` before a later unquoted ``}``
    (bash(1), Brace Expansion). One walk of the word: from the first such
    ``{`` on, a separator and then a ``}`` count as one, which reads a few
    words that bash leaves alone (``x{a}y,z}``) as expanding too — never the
    other way round. A brace without a separator stays a character.
    """
    opened: int | None = None
    separated = False
    previous: _Char | None = None
    for char in word:
        if not char.quoted:
            after_dollar = previous is not None and previous.char == "$" and not previous.quoted
            if char.char == "{" and opened is None and not after_dollar:
                opened = char.origin
            elif opened is not None and (char.char == "," or (char.char == "." and _is_dot(previous))):
                separated = True
            elif char.char == "}" and separated:
                return opened
        previous = char
    return None


def _is_dot(char: _Char | None) -> bool:
    return char is not None and char.char == "." and not char.quoted


def _assignment_end(word: list[_Char]) -> int | None:
    """Where the unquoted ``NAME=`` that makes *word* assignment-shaped ends — ``None`` where it is not."""
    match = _ASSIGNMENT.match("".join(char.char for char in word))
    if match is None or any(char.quoted for char in word[: match.end()]):
        return None
    return match.end()


def _assignment_tilde(word: list[_Char], after: int) -> int | None:
    """The origin of an unquoted ``~`` bash expands in an assignment's value — after the ``=`` or a ``:``."""
    for index in range(after, len(word)):
        char = word[index]
        if char.char == "~" and not char.quoted and (index == after or word[index - 1].char == ":"):
            return char.origin
    return None


def _lex(text: str, origins: list[int], *, argument_tilde: bool = False) -> _Lexed:
    """POSIX token recognition, then the readings that depend on a whole word.

    Reserved words and assignments count where the shell expects a command
    (:attr:`_Lexed.command_words`). An assignment's value is tilde-expanded
    after its ``=`` and each ``:``; bash outside POSIX mode does the same in
    every assignment-shaped argument (bash(1), Tilde Expansion), which
    *argument_tilde* asks for.
    """
    lexed = _Lexer(text, origins).run()
    for index, word in enumerate(lexed.words):
        brace = _brace_expansion(word)
        if brace is not None:
            lexed.add(brace, CONSTRUCT_BRACE, index)
    commands = set(lexed.command_words)
    for index, word in enumerate(lexed.words):
        _word_grammar(lexed, index, word, at_command=index in commands, argument_tilde=argument_tilde)
    return lexed


def _word_grammar(lexed: _Lexed, index: int, word: list[_Char], *, at_command: bool, argument_tilde: bool) -> None:
    """The readings of one word as a whole: a reserved word or an assignment where a command stands, and the tilde
    an assignment's value expands. An empty word (``""``) is neither, and its first character is never asked for.
    """
    end = _assignment_end(word)
    if at_command:
        if "".join(char.char for char in word) in _RESERVED and not any(char.quoted for char in word):
            lexed.add(word[0].origin, CONSTRUCT_RESERVED_WORD, index)
        if end is not None:
            lexed.add(word[0].origin, CONSTRUCT_ASSIGNMENT, index)
    tilde = _assignment_tilde(word, end) if end is not None and (at_command or argument_tilde) else None
    if tilde is not None:
        lexed.add(tilde, CONSTRUCT_TILDE, index)


def _double_quoted(text: str) -> str:
    """What the shell makes of *text* between double quotes, ``$`` and backticks aside."""
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        following = text[index + 1 : index + 2]
        if char == "\\" and following and following in _DOUBLE_QUOTED_ESCAPES:
            if following != "\n":
                out.append(following)
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _expected(value: _Value, context: Context) -> str:
    """What the first shell yields for a value in *context* where nothing breaks: the value itself unless it is an
    escaped value inside quotes, where the escaping stays in the argument for a second shell.
    """
    if not value.escaped or context == "plain":
        return value.meant
    if context == "double":
        return _double_quoted(value.meant_text)
    return value.meant_text


# Each broken value is read as one character from Unicode's supplementary
# private-use plane, which no shell treats as anything but a letter.
_SENTINEL_BASE = 0xF0000


class _Reading:
    """The shell's reading of the built string, with every value ES-DE breaks read as the value it meant."""

    def __init__(self, built: _Built) -> None:
        self.built = built
        self.broken: list[int] = []
        self.lexed = _lex(built.text, built.origins)

    def settle(self) -> None:
        """Lex until no value is newly broken — a value read in place can change how the next one reads."""
        while True:
            newly = [origin for origin in self._breaking() if origin not in self.broken]
            if not newly:
                return
            for origin in sorted(newly, key=self._span_start, reverse=True):
                self._read_as_meant(origin)
            self.lexed = _lex(self.built.text, self.built.origins)

    def _span_start(self, origin: int) -> int:
        return self.built.origins.index(origin)

    def _produced(self) -> dict[int, list[str]]:
        """What the shell yields for each segment, in order."""
        produced: dict[int, list[str]] = {}
        for word in self.lexed.words:
            for char in word:
                produced.setdefault(char.origin, []).append(char.char)
        return produced

    def _breaking(self) -> list[int]:
        built = self.built
        present = [origin for origin in dict.fromkeys(built.origins) if built.is_value(origin)]
        breaking = {origin for origin, _ in self.lexed.events if built.is_value(origin)}
        breaking.update(origin for origin in present if built.segments[origin].mutated)
        produced = self._produced()
        for origin in present:
            value = built.segments[origin].value
            if value is None or origin in self.broken:
                continue
            context = self.lexed.contexts.get(origin, "plain")
            if "".join(produced.get(origin, ())) != _expected(value, context):
                breaking.add(origin)
        return [origin for origin in present if origin in breaking]

    def _read_as_meant(self, origin: int) -> None:
        built = self.built
        if origin not in built.origins:
            return
        start = built.origins.index(origin)
        end = len(built.origins) - built.origins[::-1].index(origin)
        built.splice(start, end, chr(_SENTINEL_BASE + len(self.broken)), origin)
        self.broken.append(origin)

    def refusal(self) -> str | None:
        """The first construct the command itself reads as, ``None`` where it is plain words."""
        built = self.built
        for origin, construct in self.lexed.events:
            if built.is_value(origin) or construct in (_VALUE_SPLIT, _VALUE_QUOTE):
                continue
            return construct
        return None

    def second_shell_unsafe(self, shell_words: tuple[int, ...], *, argument_tilde: bool) -> str | None:
        """The first value handed to a second shell that would not read it as the value — its placeholder.

        *shell_words* are the words a second shell reads as commands: the
        command string a shell's ``-c`` takes. Each is read again as that shell
        reads it, the values in it together with the command's own text around
        them, and every value there must come out literal — inside one word,
        unchanged, with no reading beyond a plain word in that word or formed
        with its characters (``'./%BASENAME%_run'`` with a basename ending in
        ``$`` gives ``$_run``). Outside them, an escaped value inside quotes is
        taken as handed to a second shell too — ES-DE's backslashes are there
        for nothing else — and the text the first shell yields for it alone
        must read as one plain word equal to the value. A raw value inside
        quotes elsewhere is not, since its quotes may be read by another parser
        (MAME's command line inside a libretro argument); nor is an unquoted
        value elsewhere. A value the first shell already breaks where a second
        shell reads it is unsafe too — what it yields is not what ES-DE meant.
        """
        in_shell: set[int] = set()
        for index in shell_words:
            unsafe = self._command_string_unsafe(self.lexed.words[index], argument_tilde=argument_tilde)
            if unsafe is not None:
                return unsafe
            in_shell.update(char.origin for char in self.lexed.words[index])
        built = self.built
        produced = self._produced()
        for origin in dict.fromkeys(built.origins):
            value = built.segments[origin].value
            if value is None or origin in in_shell or not value.escaped:
                continue
            if self.lexed.contexts.get(origin, "plain") == "plain":
                continue
            if origin in self.broken:
                return value.placeholder
            yielded = "".join(produced.get(origin, ()))
            second = _lex(yielded, [_COMMAND] * len(yielded))
            if second.events or ["".join(char.char for char in word) for word in second.words] != [value.meant]:
                return value.placeholder
        return None

    def _command_string_unsafe(self, word: list[_Char], *, argument_tilde: bool) -> str | None:
        """The placeholder of the first value a shell reading *word* as a command string would not take literally."""
        built = self.built
        for char in word:
            value = built.segments[char.origin].value
            if value is not None and char.origin in self.broken:
                return value.placeholder
        second = _lex(
            "".join(char.char for char in word), [char.origin for char in word], argument_tilde=argument_tilde
        )
        holders, spelled = self._value_words(second)
        unsafe = self._event_unsafe(second, [char.origin for char in word], holders)
        if unsafe is not None:
            return unsafe
        for origin in dict.fromkeys(char.origin for char in word):
            value = built.segments[origin].value
            if value is not None and (
                len(holders.get(origin, ())) > 1 or "".join(spelled.get(origin, ())) != value.meant
            ):
                return value.placeholder
        return None

    def _value_words(self, second: _Lexed) -> tuple[dict[int, set[int]], dict[int, list[str]]]:
        """For each value in a lexing, the words its characters land in, and those characters in order."""
        holders: dict[int, set[int]] = {}
        spelled: dict[int, list[str]] = {}
        for index, inner in enumerate(second.words):
            for char in inner:
                if self.built.is_value(char.origin):
                    holders.setdefault(char.origin, set()).add(index)
                    spelled.setdefault(char.origin, []).append(char.char)
        return holders, spelled

    def _event_unsafe(self, second: _Lexed, origins: list[int], holders: dict[int, set[int]]) -> str | None:
        """The placeholder of the first value a reading beyond a plain word is formed with in the second lexing."""
        built = self.built
        valued_words = {index for held in holders.values() for index in held}
        for (origin, construct), index, span in zip(
            second.events, second.event_words, second.operator_spans, strict=True
        ):
            if built.is_value(origin):
                return self._placeholder_in(origin, index, holders)
            if span is not None:
                # An operator separates words: it touches a value only where it
                # stands right against one (``%ROMRAW%;x``, ``>%ROMRAW%``).
                beside = [origins[at] for at in (span[0] - 1, span[1]) if 0 <= at < len(origins)]
                touched = next((neighbour for neighbour in beside if built.is_value(neighbour)), None)
                if touched is not None:
                    return self._placeholder_in(touched, index, holders)
            elif construct not in (_VALUE_SPLIT, _VALUE_QUOTE) and index in valued_words:
                return self._placeholder_in(origin, index, holders)
        return None

    def _placeholder_in(self, origin: int, word: int, holders: dict[int, set[int]]) -> str:
        """The placeholder an unsafe reading is charged to: its own value's, or that of a value in its word."""
        value = self.built.segments[origin].value
        if value is None:
            touched = next(held for held, indices in holders.items() if word in indices)
            value = self.built.segments[touched].value
        return "" if value is None else value.placeholder

    def words(self) -> list[str]:
        """The words, each broken value read as the value it meant in the context it stands in."""
        words: list[str] = []
        for word in self.lexed.words:
            spelled: list[str] = []
            for char in word:
                code = ord(char.char) - _SENTINEL_BASE
                if char.origin in self.broken and 0 <= code < len(self.broken):
                    value = self.built.segments[char.origin].value
                    context = self.lexed.contexts.get(char.origin, "plain")
                    spelled.append(_expected(value, context) if value is not None else "")
                else:
                    spelled.append(char.char)
            words.append("".join(spelled))
        return words

    def broken_placeholders(self) -> tuple[str, ...]:
        names: list[str] = []
        for origin in self.broken:
            value = self.built.segments[origin].value
            if value is not None and value.placeholder not in names:
                names.append(value.placeholder)
        return tuple(names)


# --- the file's values -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Content:
    """The values ES-DE computes from the file before it edits the command (``FileData.cpp:1018-1045``)."""

    rom_raw: str
    rom_path: str
    rom_file: str
    base_name: str
    base_name_meant: str
    file_name: str
    file_name_meant: str

    @property
    def unescaped(self) -> str:
        """``romPath`` with its backslashes removed, which ES-DE takes parents of."""
        return self.rom_path.replace("\\", "")


def _content(content_path: str, view: CommandView) -> _Content | Caveat:
    """The file's values, a directory ES-DE takes as a file resolved the way it does (``:1030-1043``)."""
    rom_raw = _kept_generic(content_path)
    directory = view.is_directory(rom_raw)
    if directory == PROBE_UNKNOWN:
        return _unestablished(rom_raw)
    is_directory = directory == PROBE_HIT
    base_name = _stem(rom_raw, is_directory=is_directory, kept=False)
    base_name_meant = _stem(rom_raw, is_directory=is_directory, kept=True)
    rom_file = rom_raw
    if is_directory:
        inner = _inner_file(rom_raw, view)
        if isinstance(inner, Caveat):
            return inner
        if inner is not None:
            rom_file = inner
            base_name = base_name.split(".", 1)[0]
            base_name_meant = base_name_meant.split(".", 1)[0]
    rom_path = escaped_path(rom_file)
    return _Content(
        rom_raw=rom_raw,
        rom_path=rom_path,
        rom_file=rom_file,
        base_name=base_name,
        base_name_meant=base_name_meant,
        file_name=base_name + _extension(rom_path, kept=False),
        file_name_meant=base_name_meant + _extension(rom_file, kept=True),
    )


def _inner_file(directory: str, view: CommandView) -> str | None | Caveat:
    """The file inside *directory* that carries the directory's own name — ``None`` where none does."""
    names = view.listing(directory)
    if names is None:
        return _unestablished(directory)
    own = _file_name(directory, kept=True)
    for candidate in sorted(f"{directory}/{name}" for name in names):
        if _file_name(candidate, kept=True) != own:
            continue
        probe = view.found(candidate)
        if probe == PROBE_UNKNOWN:
            return _unestablished(candidate)
        if probe == PROBE_HIT:
            return candidate
    return None


# --- the assembly ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommandInputs:
    """What one command is assembled from beside its text: the file, the lookup's finds and the frontend's paths."""

    content_path: str
    parts: CommandParts
    home: str
    es_path: str
    rom_directory: str | None


class _Refused(Exception):
    """One reason the command is not taken apart — raised inside the assembly, answered as a refusal."""

    def __init__(self, caveat: Caveat) -> None:
        super().__init__(caveat.code)
        self.caveat = caveat


def take_apart(command: str, inputs: CommandInputs, view: CommandView) -> TakenApart:
    """The command ES-DE would run for *inputs*' file, taken apart — or the one reason it is not."""
    content = _content(inputs.content_path, view)
    if isinstance(content, Caveat):
        return TakenApart(None, (content,))
    try:
        return _Assembly(command, inputs, content, view).run()
    except _Refused as refused:
        return TakenApart(None, (refused.caveat,))


# The placeholders ES-DE resolves in a launch command, as es_systems.xml spells them.
_ROM = "%ROM%"
_ROMRAW = "%ROMRAW%"
_ROMRAWWIN = "%ROMRAWWIN%"
_BASENAME = "%BASENAME%"
_FILENAME = "%FILENAME%"
_ROMPATH = "%ROMPATH%"
_ESPATH = "%ESPATH%"
_EMUDIR = "%EMUDIR%"
_GAMEDIR = "%GAMEDIR%"
_GAMEDIRRAW = "%GAMEDIRRAW%"
_GAMEENTRYDIR = "%GAMEENTRYDIR%"
_STARTDIR = "%STARTDIR%"
_INJECT = "%INJECT%"
_RUNINBACKGROUND = "%RUNINBACKGROUND%"
_ENABLESHORTCUTS = "%ENABLESHORTCUTS%"


class _Assembly:
    """``launchGame``'s edits over one command, in its order."""

    def __init__(self, command: str, inputs: CommandInputs, content: _Content, view: CommandView) -> None:
        self.built = _Built(command)
        self.inputs = inputs
        self.content = content
        self.view = view
        self.injections: list[Injection] = []
        self.notes: list[Caveat] = []

    def run(self) -> TakenApart:
        built, content = self.built, self.content
        if built.find(_RUNINBACKGROUND) != -1:
            built.replace(_RUNINBACKGROUND, None)
            built.strip_leading()
        shortcut = False
        if built.find(_ENABLESHORTCUTS) != -1:
            shortcut = _extension(content.rom_raw, kept=False) == ".desktop"
            built.replace(_ENABLESHORTCUTS, None)
            built.strip_leading()
        quoted_core = built.find('"%CORE_') != -1
        if quoted_core:
            built.replace_command_text('"%CORE_', "%CORE_")
        built.replace("~", _raw_value("~", self.inputs.home, self.inputs.home))
        parts = self.inputs.parts
        if parts.precommand is not None:
            self._program_token("%PRECOMMAND_", parts.precommand)
        self._program_token("%EMULATOR_", parts.emulator)
        if parts.core is not None:
            self._core(parts.core, quoted=quoted_core)
        folder = self._working_folder()
        self._injections()
        if shortcut:
            raise _Refused(_desktop_file(content.rom_raw))
        self._file_values()
        self._frontend_values()
        built.strip()
        return self._read(folder)

    def _program_token(self, opener: str, found: Found) -> None:
        """``findEmulator``'s replacement of the first token (``FileData.cpp:2313-2326``, ``:2540``, ``:2589``)."""
        built = self.built
        start = built.find(opener)
        end = built.find("%", start + 1)
        if start == -1 or end == -1:
            return
        launcher = found.launcher
        if launcher.replacement_command is not None:
            built.splice(start, end + 1, found.substituted, _COMMAND)
            return
        placeholder = built.text[start : end + 1]
        if launcher.rule == RULE_SYSTEMPATH:
            value = _raw_value(placeholder, found.substituted, found.substituted)
        else:
            value = _Value(placeholder, found.substituted, launcher.path, escaped=True)
        built.splice(start, end + 1, value.actual, built.add(_Segment("value", value)))

    def _core(self, core: str, *, quoted: bool) -> None:
        """The ``%CORE_X%/name`` reference replaced with the core file (``FileData.cpp:1484-1551``)."""
        built = self.built
        start = built.find("%CORE_")
        name_start = built.find("%", start + 6)
        separator = built.find('"', name_start) if quoted else -1
        if separator == -1:
            separator = built.find(" ", name_start)
        if start == -1 or name_start == -1 or separator == -1:
            return
        placeholder = built.text[start : name_start + 1]
        escaped = " " in core
        value = _Value(placeholder, escaped_path(core) if escaped else core, core, escaped=escaped)
        end = separator + (1 if quoted else 0)
        built.splice(start, end, value.actual, built.add(_Segment("value", value)))

    def _working_folder(self) -> WorkingFolder | None:
        """``%STARTDIR%`` cut out of the command and resolved (``FileData.cpp:1570-1670``)."""
        built = self.built
        position = built.find(_STARTDIR)
        if position == -1:
            return None
        spelled = _cut_entry(built, position, _STARTDIR)
        if not spelled:
            return None
        content = self.content
        emulator = self._emulator_meant()
        actual = expand_home_path(spelled, self.inputs.home)
        meant = actual
        for placeholder, ours, its in (
            (_EMUDIR, parent_path(self.inputs.parts.emulator.substituted.replace("\\", "")), _kept_parent(emulator)),
            (_GAMEDIR, parent_path(content.unescaped), _kept_parent(content.rom_file)),
            (_GAMEENTRYDIR, content.unescaped, content.rom_file),
        ):
            actual = es_replace(actual, placeholder, ours)
            meant = es_replace(meant, placeholder, its)
        escaped = escaped_path(actual)
        read = _lex(escaped, [_COMMAND] * len(escaped))
        spelled_by_shell = ["".join(char.char for char in word) for word in read.words]
        if read.events or spelled_by_shell != [meant]:
            self.notes.append(_argument_broken(_STARTDIR))
        host = self.view.host_path(meant)
        if host is None:
            self.notes.append(_unestablished(meant))
        return WorkingFolder(meant, host, created_if_missing=True)

    def _emulator_meant(self) -> str:
        found = self.inputs.parts.emulator
        if found.launcher.replacement_command is not None:
            return found.substituted
        return found.launcher.path if found.launcher.rule != RULE_SYSTEMPATH else found.substituted

    def _injections(self) -> None:
        """Every ``%INJECT%``, read and spliced in, rescanned after each (``FileData.cpp:1672-1791``)."""
        built = self.built
        resolved = 0
        position = built.find(_INJECT)
        while position != -1:
            holder = built.origins[position]
            spelled = _cut_entry(built, position, _INJECT)
            if spelled:
                resolved += 1
                if resolved > INJECTION_LIMIT:
                    raise _Refused(_beyond_limit(LIMIT_INJECTIONS))
                self._inject(position, holder, self._inject_file(spelled))
            position = built.find(_INJECT)

    def _inject_file(self, spelled: str) -> str:
        content = self.content
        spelled = es_replace(spelled, _BASENAME, content.base_name.replace("\\", ""))
        if spelled == _ROM:
            return content.rom_raw.replace("\\", "")
        if not spelled.startswith("/"):
            return parent_path(content.unescaped) + "/" + spelled
        return spelled

    def _inject(self, position: int, holder: int, file: str) -> None:
        built = self.built
        ancestor = holder
        while built.segments[ancestor].kind == "inject":
            if built.segments[ancestor].file == file:
                raise _Refused(_inject_loop(file))
            ancestor = built.segments[ancestor].parent
        probe = self.view.found(file)
        if probe == PROBE_UNKNOWN:
            raise _Refused(_unestablished(file))
        if probe != PROBE_HIT:
            self.injections.append(Injection(file, INJECTION_ABSENT))
            return
        read = self.view.read_text_within(file, INJECT_FILE_SIZE_LIMIT)
        if read == TOO_LARGE:
            raise _Refused(_beyond_limit(LIMIT_INJECT_FILE_SIZE))
        if read is None:
            raise _Refused(_unestablished(file))
        if read.text is None:
            if read.status != READ_MISSING:
                raise _Refused(_inject_unreadable(file))
            self.injections.append(Injection(file, INJECTION_EMPTY))
            return
        arguments = "".join(line.replace("\r", "") for line in read.text.split("\n"))
        if not arguments:
            self.injections.append(Injection(file, INJECTION_EMPTY))
        elif len(arguments.encode("utf-8")) > _INJECT_LIMIT:
            self.injections.append(Injection(file, INJECTION_OVERSIZED))
        else:
            origin = built.add(_Segment("inject", file=file, parent=holder))
            built.splice(position, position, arguments + " ", origin)
            self.injections.append(Injection(file, INJECTION_INJECTED))

    def _file_values(self) -> None:
        """``%ROM%`` … ``%ROMPATH%`` (``FileData.cpp:1899-1907``)."""
        built, content, inputs = self.built, self.content, self.inputs
        built.replace(_ROM, _escaped_value(_ROM, content.rom_file, content.rom_file))
        built.replace(_BASENAME, _raw_value(_BASENAME, content.base_name, content.base_name_meant))
        built.replace(_FILENAME, _raw_value(_FILENAME, content.file_name, content.file_name_meant))
        built.replace(_ROMRAW, _raw_value(_ROMRAW, content.rom_raw, content.rom_raw))
        windows = content.rom_raw.replace("/", "\\")
        built.replace(_ROMRAWWIN, _raw_value(_ROMRAWWIN, windows, windows))
        if built.find(_ROMPATH) == -1:
            return
        if inputs.rom_directory is None:
            raise _Refused(_rom_root_unestablished())
        built.replace(_ROMPATH, _escaped_value(_ROMPATH, inputs.rom_directory, inputs.rom_directory))

    def _frontend_values(self) -> None:
        """``%ESPATH%`` … ``%GAMEDIRRAW%`` (``FileData.cpp:2092-2101``)."""
        built, content, inputs = self.built, self.content, self.inputs
        built.replace(_ESPATH, _raw_value(_ESPATH, inputs.es_path, inputs.es_path))
        emulator_dir = parent_path(inputs.parts.emulator.substituted.replace("\\", ""))
        built.replace(
            _EMUDIR,
            _Value(_EMUDIR, escaped_path(emulator_dir), _kept_parent(self._emulator_meant()), escaped=True),
        )
        game_dir = parent_path(content.unescaped)
        meant_dir = _kept_parent(content.rom_file)
        built.replace(_GAMEDIR, _Value(_GAMEDIR, escaped_path(game_dir), meant_dir, escaped=True))
        built.replace(_GAMEDIRRAW, _raw_value(_GAMEDIRRAW, game_dir, meant_dir))

    def _read(self, folder: WorkingFolder | None) -> TakenApart:
        if "\0" in self.built.text:
            # popen takes a C string (PlatformUtil.cpp:193): the shell never
            # sees what follows a NUL, which only an injected file can carry.
            raise _Refused(_shell_syntax(CONSTRUCT_NUL))
        unknown = _unknown_placeholders(self.built)
        reading = _Reading(self.built)
        reading.settle()
        construct = reading.refusal()
        if construct is not None:
            raise _Refused(_shell_syntax(construct))
        words = reading.words()
        if not words:
            raise _Refused(_no_program())
        environment, program_at = _environment(words)
        if not words[program_at]:
            raise _Refused(_no_program())
        program_origins = {char.origin for char in reading.lexed.words[program_at]}
        launched_by_shell = any(
            (value := self.built.segments[origin].value) is not None and value.placeholder == _OS_SHELL_TOKEN
            for origin in program_origins
        )
        shell_words = _shell_command_words(words, program_at, shell=launched_by_shell)
        argument_tilde = _argument_tilde(words, program_at, shell_words)
        unsafe = reading.second_shell_unsafe(shell_words, argument_tilde=argument_tilde)
        if unsafe is not None:
            raise _Refused(_second_shell_unsafe(unsafe))
        program = words[program_at]
        app_id = self.view.app_id
        launch_program = (
            LaunchProgram(VARIANT_FLATPAK, app_id=app_id, command=program)
            if app_id is not None
            else LaunchProgram(VARIANT_NATIVE, path=program)
        )
        notes = [
            *self.notes,
            *(_argument_broken(name) for name in reading.broken_placeholders()),
            *(_placeholder_unknown(name) for name in unknown),
        ]
        return TakenApart(
            LaunchCommand(
                launch_program,
                tuple(words[program_at + 1 :]),
                environment,
                folder,
                tuple(self.injections),
            ),
            tuple(notes),
        )


def _cut_entry(built: _Built, position: int, marker: str) -> str:
    """A ``%STARTDIR%=`` or ``%INJECT%=`` entry read and cut out of the command, the way ES-DE reads both.

    ``=`` must follow the marker and something must follow that; a quoted
    value runs to the closing quote and the character after it is cut too,
    an unquoted one runs to the next space (``FileData.cpp:1574-1614``,
    ``:1678-1716``).
    """
    after = position + len(marker)
    if built.byte_offset(position) + len(marker) + 2 >= built.byte_size() or built.text[after] != "=":
        raise _Refused(_entry_invalid(marker))
    if built.text[after + 1] == '"':
        closing = built.find('"', after + 2)
        if closing == -1:
            raise _Refused(_entry_invalid(marker))
        spelled = built.text[after + 2 : closing]
        built.splice(position, closing + 2)
        return spelled
    space = built.find(" ", position)
    if space == -1:
        spelled = built.text[after + 1 :]
        built.splice(position, len(built.text))
        return spelled
    spelled = built.text[after + 1 : space]
    built.splice(position, space + 1)
    return spelled


# The shells whose ``-c`` argument is a command string a second shell reads:
# by program name, or by ES-DE's own token for the operating system's shell.
_SHELLS = frozenset({"sh", "bash", "dash"})
_OS_SHELL_TOKEN = "%EMULATOR_OS-SHELL%"


# bash's long options that take the next word as their argument; every
# other long option is a flag.
_SHELL_LONG_OPTIONS_WITH_ARGUMENT = frozenset({"--rcfile", "--init-file"})


def _shell_command_words(words: list[str], program_at: int, *, shell: bool) -> tuple[int, ...]:
    """The words a shell program reads as command strings — none where the program is no shell or reads none.

    The program is a shell where its name is ``sh``, ``bash`` or ``dash``, or
    where it is what ``%EMULATOR_OS-SHELL%`` found (*shell*). Its options are
    walked the way those shells read them (bash(1), dash(1) OPTIONS): a
    ``-…`` cluster holding ``c`` sets command mode (``-ec``, ``-xc``); each
    ``o`` or ``O`` in a cluster takes the next word (``-o posix``,
    ``-O extglob``); other ``-x`` and ``+x`` letters are flags; ``--`` or a
    lone ``-`` ends the options; a long option is a flag, ``--rcfile`` and
    ``--init-file`` taking the next word. In command mode the command string
    is the first operand after the options (``sh -c -e X``, ``bash -c -- X``).
    A word the walk cannot classify makes every word after the program a
    command string, so its values are checked rather than passed. A shell
    reached any other way — a wrapper script that evaluates its argument — is
    not recognised, and a value handed to it unquoted or raw keeps only the
    first shell's reading.
    """
    if not shell and posixpath.basename(words[program_at]) not in _SHELLS:
        return ()
    command_mode = False
    index = program_at + 1
    while index < len(words):
        step = _shell_option(words[index])
        if step is None:
            return tuple(range(program_at + 1, len(words)))
        taken, sets_command, ends = step
        command_mode = command_mode or sets_command
        index += taken
        if ends:
            break
    return (index,) if command_mode and index < len(words) else ()


def _argument_tilde(words: list[str], program_at: int, shell_words: tuple[int, ...]) -> bool:
    """Whether the second shell tilde-expands assignment-shaped arguments: bash outside POSIX mode does.

    ``dash`` is a POSIX shell, and bash invoked as ``sh`` enters POSIX mode;
    ``bash``, and a shell ``%EMULATOR_OS-SHELL%`` found under another name,
    starts outside it. The options before the command string switch it, the
    last one deciding: ``--posix`` or ``-o posix`` on, ``+o posix`` off. A
    command string that may switch it off itself (``set +o posix``) — any
    that spells both ``+o`` and ``posix`` — is taken as outside it.
    """
    if not shell_words:
        return False
    name = posixpath.basename(words[program_at])
    if name == "dash":
        return False
    posix = name == "sh"
    options = words[program_at + 1 : shell_words[0]]
    for index, option in enumerate(options):
        if option == "--posix":
            posix = True
        elif option[:1] in ("-", "+") and "o" in option[1:] and options[index + 1 : index + 2] == ["posix"]:
            posix = option[0] == "-"
    command = " ".join(words[index] for index in shell_words)
    if "+o" in command and "posix" in command:
        posix = False
    return not posix


def _shell_option(word: str) -> tuple[int, bool, bool] | None:
    """One step of the option walk: the words *word* takes, whether it sets command mode, whether the options end.

    ``None`` where the word is no option the walk can read.
    """
    if word in ("--", "-"):
        return 1, False, True
    if word.startswith("--"):
        return (2 if word in _SHELL_LONG_OPTIONS_WITH_ARGUMENT else 1), False, False
    if len(word) < 2 or word[0] not in "-+":
        return 0, False, True
    letters = word[1:]
    if not (letters.isascii() and letters.isalpha()):
        return None
    return 1 + letters.count("o") + letters.count("O"), word[0] == "-" and "c" in letters, False


_PLACEHOLDER = re.compile(r"%[A-Z][A-Z0-9_-]*%")


def _unknown_placeholders(built: _Built) -> tuple[str, ...]:
    """Every ``%NAME%`` left in text the command itself or an injected file carries — ES-DE leaves it as written."""
    names: list[str] = []
    for match in _PLACEHOLDER.finditer(built.text):
        origins = built.origins[match.start() : match.end()]
        if any(built.is_value(origin) for origin in origins):
            continue
        if match.group() not in names:
            names.append(match.group())
    return tuple(names)


def _environment(words: list[str]) -> tuple[tuple[EnvironmentVariable, ...], int]:
    """A leading ``env``'s assignments, and where the program word stands.

    ``env`` reads its options first and stops at the first word that is none
    (GNU coreutils, ``getopt`` with ``+``), then takes every word holding ``=``
    as an assignment and the first other word as the program. An option is
    refused; an ``env`` with no program after its assignments runs ``env``
    itself, and is answered so.
    """
    if len(words) < 2 or words[0] != "env":
        return (), 0
    if words[1].startswith("-"):
        raise _Refused(_env_option(words[1]))
    index = 1
    assignments: list[EnvironmentVariable] = []
    while index < len(words) and "=" in words[index]:
        name, _, value = words[index].partition("=")
        if not name:
            raise _Refused(_env_option(words[index]))
        assignments.append(EnvironmentVariable(name, value))
        index += 1
    if index == len(words):
        return (), 0
    return tuple(assignments), index


# --- the caveats -------------------------------------------------------------


def _unestablished(path: str) -> Caveat:
    return Caveat(
        CAVEAT_LAUNCH_PATH_UNESTABLISHED,
        f"ES-DE would look at {path} while building the command, and atlas cannot tell what the frontend sees there",
        {"path": path},
    )


def _beyond_limit(limit: LaunchCommandLimit) -> Caveat:
    return Caveat(
        CAVEAT_LAUNCH_COMMAND_BEYOND_LIMITS,
        f"taking this command apart goes beyond what atlas follows ({limit}) — ES-DE itself bounds only what one "
        "injection splices in",
        {"limit": limit},
    )


def _rom_root_unestablished() -> Caveat:
    return Caveat(
        CAVEAT_LAUNCH_COMMAND_ROM_ROOT_UNESTABLISHED,
        "the command uses %ROMPATH%, and the ROM root ES-DE substitutes for it is not established — the "
        "answer's own caveats say why",
        {},
    )


def _second_shell_unsafe(placeholder: str) -> Caveat:
    return Caveat(
        CAVEAT_LAUNCH_COMMAND_SECOND_SHELL_UNSAFE,
        f"the command hands {placeholder} to a second shell inside quotes, and that shell would not read it as the "
        "value — ES-DE's escaping does not hold there, so the command is not taken apart",
        {"placeholder": placeholder},
    )


def _no_program() -> Caveat:
    return Caveat(
        CAVEAT_LAUNCH_COMMAND_NO_PROGRAM,
        "the command ES-DE builds is empty once read — the shell would run nothing",
        {},
    )


def _shell_syntax(construct: str) -> Caveat:
    return Caveat(
        CAVEAT_LAUNCH_COMMAND_SHELL_SYNTAX,
        f"the shell reads the command as more than plain words ({construct}) — what it runs is not a program "
        "and its arguments",
        {"construct": construct},
    )


def _env_option(option: str) -> Caveat:
    return Caveat(
        CAVEAT_LAUNCH_COMMAND_ENV_OPTION,
        f"the command's env takes {option!r} as an option or a malformed assignment, which changes how it "
        "starts the program — not followed here",
        {"option": option},
    )


def _desktop_file(path: str) -> Caveat:
    return Caveat(
        CAVEAT_LAUNCH_COMMAND_DESKTOP_FILE,
        "the command enables shortcuts and the file is a .desktop file, whose Exec line ES-DE runs instead — "
        "not taken apart here",
        {"path": path},
    )


def _entry_invalid(marker: str) -> Caveat:
    return Caveat(
        CAVEAT_LAUNCH_COMMAND_ENTRY_INVALID,
        f"the command's {marker} entry is not one ES-DE can read — it refuses the launch "
        f'("INVALID {marker} VARIABLE ENTRY")',
        {"placeholder": marker},
    )


def _inject_unreadable(file: str) -> Caveat:
    return Caveat(
        CAVEAT_LAUNCH_COMMAND_INJECT_UNREADABLE,
        f"ES-DE would splice the text of {file} into the command, and atlas cannot read it",
        {"file": file},
    )


def _inject_loop(file: str) -> Caveat:
    return Caveat(
        CAVEAT_LAUNCH_COMMAND_INJECT_LOOP,
        f"{file} injects itself again through the text it injects — ES-DE would never finish the command",
        {"file": file},
    )


def _argument_broken(placeholder: str) -> Caveat:
    return Caveat(
        CAVEAT_LAUNCH_COMMAND_ARGUMENT_BROKEN,
        f"ES-DE's substitution of {placeholder} does not reach the program as the value it stands for — the "
        "answer carries the value it meant, which the frontend itself would not pass",
        {"placeholder": placeholder},
    )


def _placeholder_unknown(placeholder: str) -> Caveat:
    return Caveat(
        CAVEAT_LAUNCH_COMMAND_PLACEHOLDER_UNKNOWN,
        f"ES-DE does not resolve {placeholder} and leaves it in the command as written; the program may read it",
        {"placeholder": placeholder},
    )
