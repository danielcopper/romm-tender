"""In-memory ``TransientUnitControl`` for service and contract tests — starts nothing."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class FakeTransientUnits:
    """Records every start, and answers the unit's state from a script.

    ``refusal`` is what :meth:`start` answers — ``None`` for a unit that
    started — and ``no_answer`` makes it raise ``TimeoutError`` instead, as a
    ``systemd-run`` that never answered does. ``states`` is what
    :meth:`is_active` answers, one per ask; once it runs out the unit is still
    running.
    """

    def __init__(
        self, *, refusal: str | None = None, states: list[bool | None] | None = None, no_answer: bool = False
    ) -> None:
        self.refusal = refusal
        self.states = list(states or [])
        self.no_answer = no_answer
        self.starts: list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]] = []
        self.asked: list[str] = []

    def start(self, unit: str, command: Sequence[str], environment: Sequence[tuple[str, str]]) -> str | None:
        self.starts.append((unit, tuple(command), tuple(environment)))
        if self.no_answer:
            raise TimeoutError("systemd-run did not answer")
        return self.refusal

    def is_active(self, unit: str) -> bool | None:
        self.asked.append(unit)
        return self.states.pop(0) if self.states else True
