"""Recording ``PluginEventSink`` implementation for plugin tests."""

from __future__ import annotations

from typing import Any


class FakeEventSink:
    """Records what was emitted, and can answer as a panel that is not there.

    ``delivers`` is the answer :meth:`emit` gives back. Setting it ``False`` is
    how a test reaches the branch where a claim goes back because nobody heard
    the event it was attached to. ``raises`` makes the send itself fail, which
    is a different case again: the event may or may not have arrived, so the
    claim is released and the failure is re-raised rather than swallowed.
    """

    def __init__(self, *, delivers: bool = True, raises: BaseException | None = None) -> None:
        self.events: list[tuple[str, Any]] = []
        self.delivers = delivers
        self.raises = raises

    async def emit(self, name: str, payload: object, /) -> bool:
        if self.raises is not None:
            raise self.raises
        self.events.append((name, payload))
        return self.delivers

    @property
    def last_payload(self) -> Any:
        """The payload of the most recent event."""
        return self.events[-1][1]

    @property
    def names(self) -> list[str]:
        """Every event name emitted, in order."""
        return [name for name, _ in self.events]
