"""In-memory ``StartCommandReader`` for service tests."""

from __future__ import annotations

from typing import Any

from domain.start_command import StartAnswer, StartCommand

# The command a fake answers for every question nobody stated an answer for.
DEFAULT_COMMAND = StartCommand(argv=("/usr/bin/emulator", "/roms/game"), folder=None)


class FakeStartCommands:
    """Answers the resolver's launch command per entry label, recording each question.

    ``answers`` maps a label to the answer for it; any other label is answered
    with *default*. ``asked`` records ``(system, content_path, label)`` for
    each question.
    """

    def __init__(
        self, *, default: StartAnswer = DEFAULT_COMMAND, answers: dict[str, StartAnswer] | None = None
    ) -> None:
        self.default = default
        self.answers: dict[str, StartAnswer] = answers if answers is not None else {}
        self.asked: list[tuple[str, str, str]] = []

    def start_command(self, system: str, content_path: str, *, label: str, **_asked: Any) -> StartAnswer:
        self.asked.append((system, content_path, label))
        return self.answers.get(label, self.default)
