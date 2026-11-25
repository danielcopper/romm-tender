"""What only the process hosting the backend knows about its own run.

Contract: the facts about this backend's own run that no service knows and no
event announces, in a record the runtime fills in as start-up proceeds.

:class:`SteamReadings` and :class:`SteamUrls` are the parts of it the
application uses: what only the injector can see of Steam, and the one thing
the application asks it to do there. Each is created before the services,
which are handed it, and filled in once the injector exists — the services are
built before it, and may not import this package to reach it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from host.inject.recovery import ListedApp


class SteamReadings:
    """What the injector can read of Steam, for the application. Answers nothing until it is attached."""

    def __init__(self) -> None:
        self._running_apps: Callable[[], Awaitable[tuple[ListedApp, ...] | None]] | None = None
        self._reload_frees_at: Callable[[], Awaitable[float | None]] | None = None

    def attach(
        self,
        *,
        running_apps: Callable[[], Awaitable[tuple[ListedApp, ...] | None]],
        reload_frees_at: Callable[[], Awaitable[float | None]],
    ) -> None:
        """Answer from now on through the injector's own readings."""
        self._running_apps = running_apps
        self._reload_frees_at = reload_frees_at

    async def running_apps(self) -> tuple[ListedApp, ...] | None:
        """Steam's running apps; ``None`` where no reading could be taken, as before anything is attached."""
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


class SteamUrls:
    """Hands Steam a ``steam://`` URL through the injector, for the application. Sends nothing until it is attached."""

    def __init__(self) -> None:
        self._execute: Callable[[str], Awaitable[bool]] | None = None

    def attach(self, *, execute_steam_url: Callable[[str], Awaitable[bool]]) -> None:
        """Send from now on through the injector's attachment to Steam."""
        self._execute = execute_steam_url

    async def execute_steam_url(self, url: str) -> bool:
        """Whether *url* went out; ``False`` before anything is attached, as when the panel is not loaded at all."""
        if self._execute is None:
            return False
        return await self._execute(url)


@dataclass
class HostStatus:
    """The host's own record of this run, filled in as start-up proceeds."""

    port: int = 0
    failed_startup_steps: list[str] = field(default_factory=list)

    steam: SteamReadings = field(default_factory=SteamReadings)
    steam_urls: SteamUrls = field(default_factory=SteamUrls)

    def record_failed_step(self, name: str) -> None:
        """Note that the start-up step *name* did not finish."""
        self.failed_startup_steps.append(name)
