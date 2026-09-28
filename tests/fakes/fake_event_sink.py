"""A recording event sink whose ``emit`` is the ``EventEmitter`` plugin tests hand ``_main``."""

from __future__ import annotations

from typing import Any


class FakeEventSink:
    """Records what was emitted, and can answer as a panel that is not there.

    ``delivers`` is the answer :meth:`emit` gives back; setting it ``False`` is
    how a test reaches the branch where nobody heard the event.
    """

    def __init__(self, *, delivers: bool = True) -> None:
        self.events: list[tuple[str, Any]] = []
        self.delivers = delivers

    async def emit(self, name: str, payload: object, /) -> bool:
        self.events.append((name, payload))
        return self.delivers
