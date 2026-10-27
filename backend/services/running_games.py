"""RunningGames — which of Tender's games Steam lists as running right now.

The one reading the backend takes of Steam's running apps
(``SteamInterfaceReader.running_apps``), matched to ROMs by the shortcut app id
``roms.shortcut_app_id`` records. What counts as running is that reading's own
rule, and why the save code asks it is
``docs/architecture/save-file-sync-architecture.md``'s, "Is the game running".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio

    from services.protocols import DebugLogger, SteamInterfaceReader, UnitOfWorkFactory


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

    async def running_rom_ids(self) -> frozenset[int]:
        """The ROMs whose shortcut Steam lists as running; empty where no reading could be taken.

        A reader that raised took no reading, and an app Steam listed without
        an id matches no ROM.
        """
        try:
            apps = await self._steam.running_apps()
        except Exception as e:
            self._log_debug(f"Steam's running apps could not be read: {e!r}")
            return frozenset()
        if apps is None:
            return frozenset()
        app_ids = {app.app_id for app in apps if app.app_id is not None}
        if not app_ids:
            return frozenset()
        return await self._loop.run_in_executor(None, self._roms_of, app_ids)

    def _roms_of(self, app_ids: set[int]) -> frozenset[int]:
        with self._uow_factory() as uow:
            roms = [uow.roms.get_by_app_id(app_id) for app_id in app_ids]
        return frozenset(rom.rom_id for rom in roms if rom is not None)
