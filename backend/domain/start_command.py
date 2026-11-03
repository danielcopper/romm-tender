"""What one game's start comes to, as the resolver's launch command states it, in Tender's words.

The command is the resolver's, read in :mod:`adapters.atlas_launch`; this is
the value it is read into. A complete command is a :class:`StartCommand`, the
program and its arguments as Tender runs them, with the folder it starts in;
anything else is a :class:`StartRefused` naming why the resolver gives no
complete command (#2221 D2, D13). The panel words each reason
(``frontend/src/utils/emulatorStart.ts``).

Pure compute — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Why the resolver gives no complete command for an entry, in Tender's words.
# How ES-DE starts it could not be read: shell syntax beyond plain words, an
# ``env`` option, an entry ES-DE itself refuses, a value a second shell would
# not read as itself, a file that injects itself again, or any refusal Tender
# has no word of its own for.
COMMAND_UNREAD = "command_unread"
# The command parses a ``.desktop`` file.
DESKTOP_FILE = "desktop_file"
# A file the command reads in could not be read.
INJECTED_FILE_UNREAD = "injected_file_unread"
# The command names no program.
NO_PROGRAM = "no_program"
# Where the source keeps its games could not be established.
ROM_ROOT_UNKNOWN = "rom_root_unknown"
# The command is longer than the resolver follows.
COMMAND_TOO_LONG = "command_too_long"
# Which ES-DE build the source ships could not be read.
FRONTEND_BUILD_UNREAD = "frontend_build_unread"
# What the program needs from the distribution's own start could not be read
# (#2221 D13), or is not stated at all.
ENVIRONMENT_UNREAD = "environment_unread"
ENVIRONMENT_UNKNOWN = "environment_unknown"
# The folder the command starts in could not be placed on the host.
FOLDER_UNPLACED = "folder_unplaced"
# The label the start was asked with names no entry of the system.
ENTRY_UNKNOWN = "entry_unknown"

# What is said beside a complete command: a placeholder ES-DE leaves in the
# command as written (#2708 Q3).
PLACEHOLDER_UNFILLED = "placeholder_unfilled"


@dataclass(frozen=True, slots=True)
class StartNote:
    """One thing said beside a command that starts: its word, and the placeholder it names."""

    reason: str
    placeholder: str | None = None

    def payload(self) -> dict[str, Any]:
        """The note as the panel reads it."""
        return {"reason": self.reason, "placeholder": self.placeholder}


@dataclass(frozen=True, slots=True)
class StartCommand:
    """A complete command, as Tender runs it.

    ``argv`` is the program and its arguments, the folder grants and the
    environment already written in. ``folder`` is the folder the command names
    to start in, on the host, ``None`` where it names none; ``create_folder``
    whether the frontend creates that folder where it is missing. ``notes`` are
    what is said beside the start.
    """

    argv: tuple[str, ...]
    folder: str | None
    create_folder: bool = False
    notes: tuple[StartNote, ...] = ()

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("StartCommand: argv must name a program")


@dataclass(frozen=True, slots=True)
class StartRefused:
    """No complete command: ``reason`` is why, one of this module's words or an entry's installation word."""

    reason: str


StartAnswer = StartCommand | StartRefused


def start_verdict(answer: StartAnswer) -> dict[str, Any]:
    """*answer* as a surface reads it: ``{starts, reason, remarks}``.

    ``reason`` is ``None`` and ``remarks`` the notes beside the start where the
    command is complete; otherwise ``starts`` is ``False``, ``reason`` says why
    and ``remarks`` is empty.
    """
    if isinstance(answer, StartRefused):
        return {"starts": False, "reason": answer.reason, "remarks": []}
    return {"starts": True, "reason": None, "remarks": [note.payload() for note in answer.notes]}
