"""The save sync's parts for a game Steam lists as running: asking, naming, the per-ROM stops, and a run's skips.

What the save paths do with a running game is
``docs/architecture/save-file-sync-architecture.md``'s, "A game that is running".
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from services.saves._refusals import game_running
from services.saves._sync_stop import SyncStop

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Iterator

    from services.protocols import RunningRomIdsFn, UnitOfWorkFactory

# A ROM with no registry row still needs a name in a sentence.
_UNNAMED_GAME = "This game"


class RunningGameGuard:
    """Asks whether a ROM's game runs, and names it; read afresh on every call."""

    def __init__(
        self,
        *,
        running_rom_ids: RunningRomIdsFn,
        uow_factory: UnitOfWorkFactory,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._running_rom_ids = running_rom_ids
        self._uow_factory = uow_factory
        self._loop = loop

    async def is_running(self, rom_id: int) -> bool:
        """Whether Steam lists *rom_id*'s shortcut as running right now."""
        return rom_id in await self._running_rom_ids()

    async def name_of(self, rom_id: int) -> str:
        """The name a sentence calls *rom_id*'s game by."""
        return await self._loop.run_in_executor(None, self._name_io, rom_id)

    async def refuse_if_running(self, rom_id: int, **details: Any) -> None:
        """Raise ``game_running``, naming the game and carrying *details*, while *rom_id*'s game runs."""
        if await self.is_running(rom_id):
            raise game_running(await self.name_of(rom_id), **details)

    def _name_io(self, rom_id: int) -> str:
        with self._uow_factory() as uow:
            rom = uow.roms.get(rom_id)
        return rom.name if rom is not None and rom.name else _UNNAMED_GAME


class RomSyncStops:
    """The stops of the syncs that register here — a sweep's sync of a ROM and a Sync Saves — by ROM.

    A start's stop reaches them too, so the sync of a game that is about to
    start ends at its next check rather than writing under it.
    """

    def __init__(self) -> None:
        self._stops: dict[int, list[SyncStop]] = {}

    @contextmanager
    def track(self, rom_id: int) -> Iterator[SyncStop]:
        """A stop for *rom_id*, registered for as long as the body runs."""
        stop = SyncStop()
        self._stops.setdefault(rom_id, []).append(stop)
        try:
            yield stop
        finally:
            stops = self._stops[rom_id]
            stops.remove(stop)
            if not stops:
                del self._stops[rom_id]

    def stop(self, rom_id: int) -> bool:
        """Ask every sync of *rom_id* tracked here to stop; whether there was one."""
        stops = self._stops.get(rom_id, [])
        for stop in stops:
            stop.request()
        return bool(stops)


@dataclass
class RunningSkips:
    """The games a run over several ROMs passed over because they were running, in the order it reached them."""

    skipped: list[tuple[int, str]] = field(default_factory=list)

    def skip(self, rom_id: int, name: str) -> None:
        """Record that the run passed *rom_id*'s game over."""
        self.skipped.append((rom_id, name))

    @property
    def rom_ids(self) -> list[int]:
        """The ROMs passed over."""
        return [rom_id for rom_id, _name in self.skipped]

    def as_list(self) -> list[dict[str, Any]]:
        """The ROMs passed over, as a result carries them."""
        return [{"rom_id": rom_id, "name": name} for rom_id, name in self.skipped]

    def annotate(self, message: str) -> str:
        """*message*, with a clause naming the games passed over where there were any."""
        if not self.skipped:
            return message
        names = [name for _rom_id, name in self.skipped]
        if len(names) == 1:
            return f"{message}; {names[0]} was skipped because it is running"
        return f"{message}; {', '.join(names[:-1])} and {names[-1]} were skipped because they are running"
