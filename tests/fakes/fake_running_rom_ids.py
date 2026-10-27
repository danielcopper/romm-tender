"""In-memory ``RunningRomIdsFn`` for service tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


class FakeRunningRomIds:
    """Answers the ROMs in ``running`` as the games Steam lists as running, and counts the readings.

    ``running`` is a set a test changes between calls to start or quit a game;
    ``on_read``, where set, is called with the reading's number before it
    answers, so a test can start a game between two readings.
    """

    def __init__(self, running: Iterable[int] = ()) -> None:
        self.running: set[int] = set(running)
        self.readings = 0
        self.on_read: Callable[[int], None] | None = None

    async def __call__(self) -> frozenset[int]:
        self.readings += 1
        if self.on_read is not None:
            self.on_read(self.readings)
        return frozenset(self.running)
