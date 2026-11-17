"""RunningGames — which of Tender's games Steam lists as running right now.

The one reading the backend takes of Steam's running apps
(``SteamInterfaceReader.running_apps``), matched to ROMs by the shortcut app id
``roms.shortcut_app_id`` records. What counts as running is
``docs/architecture/save-file-sync-architecture.md``'s, "Is the game running",
and why the save code asks it, "A game that is running".
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.protocols import DebugLogger, SteamInterfaceReader, UnitOfWorkFactory

# Why this is shorter than the host's own limit per debugger command:
# save-file-sync-architecture.md, "Is the game running".
READING_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class RunningGamesConfig:
    """Frozen wiring bundle handed to ``RunningGames.__init__``.

    ``steam`` is the host's reading of Steam's interface; ``uow_factory`` reads
    which ROM a listed app id is the shortcut of.
    """

    steam: SteamInterfaceReader
    uow_factory: UnitOfWorkFactory
    loop: asyncio.AbstractEventLoop
    log_debug: DebugLogger


class RunningGames:
    """Answers which ROMs Steam lists as running, read afresh on every call."""

    def __init__(self, *, config: RunningGamesConfig) -> None:
        self._steam = config.steam
        self._uow_factory = config.uow_factory
        self._loop = config.loop
        self._log_debug = config.log_debug

    async def running_rom_ids(self) -> frozenset[int] | None:
        """The ROMs whose shortcut Steam lists as running; ``None`` where no reading could be taken. Never raises.

        No reading is taken where the host has none (no debugger, the panel
        not loaded), where the reader raised or did not answer within
        :data:`READING_TIMEOUT_SECONDS`, or where the read of the ROMs failed;
        each is logged here. Why the injector took none it logs itself; before
        the injection is set up the host answers none without a reason. An app
        Steam listed without an id matches no ROM. What each reader does without a reading is
        ``docs/architecture/save-file-sync-architecture.md``'s, "Is the game
        running".
        """
        try:
            async with asyncio.timeout(READING_TIMEOUT_SECONDS):
                apps = await self._steam.running_apps()
        except Exception as e:
            self._log_debug(f"Steam's running apps could not be read: {e!r}")
            return None
        if apps is None:
            self._log_debug("Steam's running apps could not be read: the host took no reading")
            return None
        app_ids = {app.app_id for app in apps if app.app_id is not None}
        if not app_ids:
            return frozenset()
        try:
            return await self._loop.run_in_executor(None, self._roms_of, app_ids)
        except Exception as e:
            self._log_debug(f"The ROMs of Steam's running apps could not be read: {e!r}")
            return None

    def _roms_of(self, app_ids: set[int]) -> frozenset[int]:
        with self._uow_factory() as uow:
            roms = [uow.roms.get_by_app_id(app_id) for app_id in app_ids]
        return frozenset(rom.rom_id for rom in roms if rom is not None)
