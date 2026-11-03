"""Atlas launch adapter — the one place the resolver's launch command is read.

The resolver answers how the frontend would start one game with one entry:
the program and how it is started, the arguments with every placeholder
resolved, the environment a leading ``env`` sets, the folder it starts in, and
what the program reads from its distribution's own start
(``distribution_environment``). This module reads that answer into Tender's
view (:mod:`domain.start_command`): a complete command as Tender runs it, or
why there is none (#2221 D2). Nothing else in Tender reads the answer.

**Complete** is the resolver's answer with nothing missing: a command, the
distribution's environment stated (``null`` is ``launch-environment-unpinned``,
#2708 Q2), every variable in it with a value (#2221 D13 — ``""`` is a value,
passed on as set and empty), and a working folder placed on the host where the
command names one. Whether a variable is ``provided`` gates nothing: it is
passed on as stated (#2708 Q10).

**What Tender adds** to a Flatpak start is its own, not the resolver's: no
Wayland socket (#2221 D18), the game's folder granted writable and Tender's
firmware store read-only (D4), each for this start only. The distribution's
environment is set first and the command's own ``env`` after it, so the
command wins on a shared name, as ES-DE's ``env`` does inside RetroDECK's
start.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from _vendor.atlas import (
    AVAILABILITY_STARTABLE,
    CAVEAT_FRONTEND_BUILD_SEALED,
    CAVEAT_FRONTEND_BUILD_UNPINNED,
    CAVEAT_LAUNCH_COMMAND_ARGUMENT_BROKEN,
    CAVEAT_LAUNCH_COMMAND_BEYOND_LIMITS,
    CAVEAT_LAUNCH_COMMAND_DESKTOP_FILE,
    CAVEAT_LAUNCH_COMMAND_ENTRY_INVALID,
    CAVEAT_LAUNCH_COMMAND_ENV_OPTION,
    CAVEAT_LAUNCH_COMMAND_INJECT_LOOP,
    CAVEAT_LAUNCH_COMMAND_INJECT_UNREADABLE,
    CAVEAT_LAUNCH_COMMAND_LABEL_UNKNOWN,
    CAVEAT_LAUNCH_COMMAND_NO_PROGRAM,
    CAVEAT_LAUNCH_COMMAND_PLACEHOLDER_UNKNOWN,
    CAVEAT_LAUNCH_COMMAND_ROM_ROOT_UNESTABLISHED,
    CAVEAT_LAUNCH_COMMAND_SECOND_SHELL_UNSAFE,
    CAVEAT_LAUNCH_COMMAND_SHELL_SYNTAX,
    CAVEAT_LAUNCH_ENVIRONMENT_UNPINNED,
    CAVEAT_LAUNCH_ENVIRONMENT_UNREAD,
    CAVEAT_LAUNCHER_SCRIPT_UNPINNED,
    VARIANT_FLATPAK,
)

from adapters.atlas_catalogue import installation_word
from domain.start_command import (
    COMMAND_TOO_LONG,
    COMMAND_UNREAD,
    DESKTOP_FILE,
    ENTRY_UNKNOWN,
    ENVIRONMENT_UNKNOWN,
    ENVIRONMENT_UNREAD,
    FOLDER_UNPLACED,
    FRONTEND_BUILD_UNREAD,
    INJECTED_FILE_UNREAD,
    NO_PROGRAM,
    PLACEHOLDER_UNFILLED,
    ROM_ROOT_UNKNOWN,
    StartAnswer,
    StartCommand,
    StartNote,
    StartRefused,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from adapters.emulator_sources import EmulatorSourcesAdapter
    from domain.emulator_sources import SourcesReading

# Why the resolver gives no command, each in Tender's word; a refusal this map
# does not hold reads as :data:`COMMAND_UNREAD`. An EmuDeck launcher script no
# reading is pinned for is such a refusal too: Tender starts no game through
# EmuDeck yet.
_REFUSED_BECAUSE = {
    CAVEAT_LAUNCH_COMMAND_SHELL_SYNTAX: COMMAND_UNREAD,
    CAVEAT_LAUNCH_COMMAND_ENV_OPTION: COMMAND_UNREAD,
    CAVEAT_LAUNCH_COMMAND_ENTRY_INVALID: COMMAND_UNREAD,
    CAVEAT_LAUNCH_COMMAND_SECOND_SHELL_UNSAFE: COMMAND_UNREAD,
    CAVEAT_LAUNCH_COMMAND_INJECT_LOOP: COMMAND_UNREAD,
    CAVEAT_LAUNCHER_SCRIPT_UNPINNED: COMMAND_UNREAD,
    CAVEAT_LAUNCH_COMMAND_DESKTOP_FILE: DESKTOP_FILE,
    CAVEAT_LAUNCH_COMMAND_INJECT_UNREADABLE: INJECTED_FILE_UNREAD,
    CAVEAT_LAUNCH_COMMAND_NO_PROGRAM: NO_PROGRAM,
    CAVEAT_LAUNCH_COMMAND_ROM_ROOT_UNESTABLISHED: ROM_ROOT_UNKNOWN,
    CAVEAT_LAUNCH_COMMAND_BEYOND_LIMITS: COMMAND_TOO_LONG,
    CAVEAT_FRONTEND_BUILD_UNPINNED: FRONTEND_BUILD_UNREAD,
    CAVEAT_FRONTEND_BUILD_SEALED: FRONTEND_BUILD_UNREAD,
    CAVEAT_LAUNCH_COMMAND_LABEL_UNKNOWN: ENTRY_UNKNOWN,
}

# Notes the command's own values already say: a distribution environment not
# stated, and a variable stated without a value, each read off the command
# itself (:func:`_incomplete_because`) rather than off its note.
_SAID_BY_THE_COMMAND = frozenset({CAVEAT_LAUNCH_ENVIRONMENT_UNPINNED, CAVEAT_LAUNCH_ENVIRONMENT_UNREAD})

# A value ES-DE's own substitution would break, answered as the value it means:
# Tender passes that value and says nothing (#2708 C4).
_CHANGES_NOTHING = frozenset({CAVEAT_LAUNCH_COMMAND_ARGUMENT_BROKEN})

# The program Flatpak starts a sandboxed command with, and what Tender adds to
# every such start (#2221 D4, D18).
_FLATPAK_RUN = ("flatpak", "run", "--nosocket=wayland")

# The program that sets a host program's environment.
_ENV = "env"


def _refusal(answer: Any) -> StartRefused:
    """Why *answer* gives no command: the chosen entry's installation where it cannot start, else the refusal."""
    entry = answer.entry
    if entry is not None and entry.availability != AVAILABILITY_STARTABLE:
        word = installation_word(entry)
        if word is not None:
            return StartRefused(word)
    reason = next((_REFUSED_BECAUSE[c.code] for c in answer.caveats if c.code in _REFUSED_BECAUSE), COMMAND_UNREAD)
    return StartRefused(reason)


def _incomplete_because(command: Any) -> str | None:
    """Why a command the resolver answers is not complete, or ``None`` where it is."""
    variables = command.distribution_environment
    if variables is None:
        return ENVIRONMENT_UNKNOWN
    if any(variable.value is None for variable in variables):
        return ENVIRONMENT_UNREAD
    folder = command.working_folder
    if folder is not None and folder.host_path is None:
        return FOLDER_UNPLACED
    return None


def _notes(answer: Any) -> tuple[StartNote, ...]:
    """What is said beside a command that starts: each placeholder ES-DE leaves unfilled."""
    return tuple(
        StartNote(PLACEHOLDER_UNFILLED, caveat.data.get("placeholder"))
        for caveat in answer.caveats
        if caveat.code == CAVEAT_LAUNCH_COMMAND_PLACEHOLDER_UNKNOWN
    )


def _grant(folder: str, *, read_only: bool) -> str:
    """*folder* as a ``flatpak run --filesystem`` option, its ``\\`` and ``:`` escaped as Flatpak reads them."""
    escaped = folder.replace("\\", "\\\\").replace(":", "\\:")
    return f"--filesystem={escaped}:ro" if read_only else f"--filesystem={escaped}"


def _environment(command: Any) -> list[tuple[str, str]]:
    """The variables the program is started with: the distribution's first, the command's own after them."""
    return [
        *((variable.name, variable.value) for variable in command.distribution_environment),
        *((variable.name, variable.value) for variable in command.environment),
    ]


def _argv(command: Any, *, writable: tuple[str, ...], read_only: tuple[str, ...]) -> tuple[str, ...]:
    """The program and its arguments as Tender runs them."""
    program = command.program
    environment = _environment(command)
    if program.variant == VARIANT_FLATPAK:
        return (
            *_FLATPAK_RUN,
            *(_grant(folder, read_only=False) for folder in writable),
            *(_grant(folder, read_only=True) for folder in read_only),
            *(f"--env={name}={value}" for name, value in environment),
            f"--command={program.command}",
            program.app_id,
            *command.arguments,
        )
    prefix = (_ENV, *(f"{name}={value}" for name, value in environment)) if environment else ()
    return (*prefix, program.path, *command.arguments)


def read_start(answer: Any, *, writable: tuple[str, ...] = (), read_only: tuple[str, ...] = ()) -> StartAnswer:
    """*answer* in Tender's words, the folders in *writable* and *read_only* granted to a Flatpak start."""
    command = answer.command
    if command is None:
        return _refusal(answer)
    incomplete = _incomplete_because(command)
    if incomplete is not None:
        return StartRefused(incomplete)
    folder = command.working_folder
    return StartCommand(
        argv=_argv(command, writable=writable, read_only=read_only),
        folder=folder.host_path if folder is not None else None,
        create_folder=folder.created_if_missing if folder is not None else False,
        notes=_notes(answer),
    )


class AtlasLaunchAdapter:
    """Asks the answering emulator source for one game's launch command, and reads it into Tender's view.

    Keeps no answer of its own: each is asked through a reading of the emulator
    sources and kept by that reading alone.
    """

    def __init__(self, *, sources: EmulatorSourcesAdapter, log_debug: Callable[[str], None]) -> None:
        self._sources = sources
        self._log_debug = log_debug

    def start_command(
        self,
        system: str,
        content_path: str,
        *,
        label: str,
        writable: tuple[str, ...] = (),
        read_only: tuple[str, ...] = (),
        reading: SourcesReading | None = None,
    ) -> StartAnswer:
        """How *content_path* starts as *system*'s content with the entry *label* names, or why it does not.

        Asked of the answering source with the entry Tender starts the game
        through. Where no source answers or the resolver could not be asked, the
        command could not be read (:data:`~domain.start_command.COMMAND_UNREAD`).
        """
        reading = reading if reading is not None else self._sources.read()
        installation = reading.answering_installation()
        if installation is None:
            self._log_debug(f"[launch] no emulator source answers ({reading.no_answer_reason()})")
            return StartRefused(COMMAND_UNREAD)

        def ask() -> Any:
            try:
                return installation.launch_command(system, content_path, label=label)
            except Exception as exc:
                self._log_debug(f"[launch] resolver failed on launch_command({system!r}, label={label!r}): {exc!r}")
                return None

        answer = reading.remember(("launch_command", system, content_path, label), ask)
        if answer is None:
            return StartRefused(COMMAND_UNREAD)
        codes = sorted({caveat.code for caveat in answer.caveats} - _CHANGES_NOTHING)
        self._log_debug(f"[launch] {system}/{label}: command={answer.command is not None} caveats={codes}")
        return read_start(answer, writable=writable, read_only=read_only)
