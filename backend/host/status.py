"""What the panel can ask about the process hosting it.

Contract: the facts about this backend's own run that no service knows and no
event announces. It is a record the runtime fills in and an endpoint reads — one
number each, read when the panel opens.

Two of them exist because the alternative is a fault that lives only in a log
file. A start-up routine that fails on every start would otherwise never be
noticed by anyone who did not go looking, and a panel and backend that disagree
about the protocol would show as nothing at all — the messages are dropped and
the connection is deliberately kept. Neither is worth an event: an event is a
statement about a moment, and both of these are states.

:class:`SteamReadings` is the one part of it the application reads rather than
the panel: what only the injector can see of Steam. It is created before the
services, which are handed it, and filled in once the injector exists — the
services are built before it, and may not import this package to reach it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def _no_messages_dropped() -> int:
    """Before a server exists, nothing has been dropped."""
    return 0


class SteamReadings:
    """What the injector can read of Steam, for the application. Answers nothing until it is attached."""

    def __init__(self) -> None:
        self._running_apps: Callable[[], Awaitable[tuple[str, ...] | None]] | None = None
        self._reload_frees_at: Callable[[], Awaitable[float | None]] | None = None

    def attach(
        self,
        *,
        running_apps: Callable[[], Awaitable[tuple[str, ...] | None]],
        reload_frees_at: Callable[[], Awaitable[float | None]],
    ) -> None:
        """Answer from now on through the injector's own readings."""
        self._running_apps = running_apps
        self._reload_frees_at = reload_frees_at

    async def running_apps(self) -> tuple[str, ...] | None:
        """Steam's running apps by name; ``None`` where no reading could be taken, as before anything is attached."""
        if self._running_apps is None:
            return None
        return await self._running_apps()

    async def reload_frees_at(self) -> float | None:
        """When Steam's interface may be taken down once more; ``None`` while it may be now.

        Before anything is attached nothing here takes the interface down, so
        nothing waits on the limit.
        """
        if self._reload_frees_at is None:
            return None
        return await self._reload_frees_at()


@dataclass
class HostStatus:
    """The host's own answer about this run. Filled in as start-up proceeds."""

    port: int = 0
    failed_startup_steps: list[str] = field(default_factory=list)

    # Read rather than stored, because the count goes on changing for as long as
    # the connection lives: a number copied in here at start-up would answer 0
    # for the rest of the run, which is exactly the reading this counter exists
    # to contradict.
    count_dropped_messages: Callable[[], int] = _no_messages_dropped

    steam: SteamReadings = field(default_factory=SteamReadings)

    def record_failed_step(self, name: str) -> None:
        """Note that the start-up step *name* did not finish."""
        self.failed_startup_steps.append(name)
