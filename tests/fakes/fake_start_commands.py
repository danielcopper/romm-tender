"""In-memory ``StartCommandReader`` for service tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.start_command import StartAnswer, StartCommand

if TYPE_CHECKING:
    from domain.emulator_sources import SourcesReading
    from domain.launchability import Launchability
    from domain.platform_system import SystemArgument

# The command a fake answers for every question nobody stated an answer for.
DEFAULT_COMMAND = StartCommand(argv=("/usr/bin/emulator", "/roms/game"), folder=None)


class FakeStartCommands:
    """Answers the resolver's launch command per entry label, recording each question.

    ``answers`` maps a label to the answer for it; any other label is answered
    with *default*. ``asked`` records ``(system, path, label)`` for each
    question — the path the command is asked with, the verdict's
    ``start_path`` — and ``readings`` the reading each was asked in.
    """

    def __init__(
        self, *, default: StartAnswer = DEFAULT_COMMAND, answers: dict[str, StartAnswer] | None = None
    ) -> None:
        self.default = default
        self.answers: dict[str, StartAnswer] = answers if answers is not None else {}
        self.asked: list[tuple[SystemArgument, str, str]] = []
        self.readings: list[SourcesReading | None] = []

    def start_command(
        self, system: SystemArgument, launch: Launchability, *, reading: SourcesReading | None = None, **_asked: Any
    ) -> StartAnswer:
        self.asked.append((system, launch.start_path, launch.entry))
        self.readings.append(reading)
        return self.answers.get(launch.entry, self.default)
