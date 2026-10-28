"""One ROM's step of the whole-library sweep: whether it is passed over, its one reading, and its stop.

What the sweep does with a game that is running is
``docs/architecture/save-file-sync-architecture.md``'s, "A game that is running".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from services.saves._sync_stop import SyncStopped

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable

    from domain.save_answer import SaveAnswer
    from services.protocols import DebugLogger
    from services.saves._sync_stop import SyncStop
    from services.saves.sync_engine._running_game import RomSyncStops, RunningGameGuard, RunningSkips
    from services.saves.sync_engine._shape_refusal import ContentDirTally

type RomSyncResult = tuple[int, int, list[str], list[dict[str, Any]]]


async def sweep_rom(
    rom_id: int,
    *,
    stops: RomSyncStops,
    lock: asyncio.Lock,
    sync: Callable[..., Awaitable[RomSyncResult]],
    running: RunningGameGuard,
    running_skips: RunningSkips,
) -> RomSyncResult:
    """Run *sync* for *rom_id* under *lock*, handing it a stop a start of the game reaches.

    A sync its stop ended counts what moved before the stop, and the game is
    named in *running_skips*.
    """
    with stops.track(rom_id) as stop:
        try:
            async with lock:
                result = await sync(stop=stop)
        except SyncStopped:
            result = 0, 0, [], []
    if stop.stopped_the_sync():
        running_skips.skip(rom_id, await running.name_of(rom_id))
    return result


async def sweep_reading(
    rom_id: int,
    *,
    running: RunningGameGuard,
    read_answer: Callable[[], Awaitable[SaveAnswer | None]],
    follow: Callable[[SaveAnswer | None], Awaitable[None]],
    running_skips: RunningSkips | None,
    stop: SyncStop | None,
    content_dir_tally: ContentDirTally | None,
    log_debug: DebugLogger,
) -> tuple[bool, SaveAnswer | None]:
    """The sweep's one reading of *rom_id*, after *follow* carried its files where the reading puts them.

    Answers ``(True, None)`` for a ROM whose game is running, which it records
    into *running_skips*, and raises :class:`SyncStopped` where *stop* was asked
    before the follow. *follow* takes no second reading of Steam: this one asks
    under the same lock a moment before, and a start since is the stop's.
    """
    if running_skips is not None and await running.is_running(rom_id):
        running_skips.skip(rom_id, await running.name_of(rom_id))
        log_debug(f"_run_rom_sync({rom_id}): its game is running, skipping bulk sync")
        return True, None
    save_answer = await read_answer()
    if stop is not None and stop.stops_here():
        raise SyncStopped
    await follow(save_answer)
    if content_dir_tally is not None:
        content_dir_tally.count(save_answer)
    return False, save_answer
