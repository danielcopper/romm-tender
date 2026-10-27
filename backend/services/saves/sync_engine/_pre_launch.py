"""What the sync before a start is doing, and the stop the panel can ask of it."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from lib.errors import NamedRefused
from services.saves._sync_stop import SyncStop, SyncStopped

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from services.saves.sync_engine._gate import GateHolder

PRE_LAUNCH_SYNC_STOPPED = "Save sync stopped before the game started"


class PreLaunchState(StrEnum):
    """What the sync before a start of one ROM is doing."""

    IDLE = "idle"
    WAITING_FOR_SYNC = "waiting_for_sync"
    WAITING_FOR_ROM = "waiting_for_rom"
    SYNCING = "syncing"


class PreLaunchSyncStopped(NamedRefused):
    """A sync before a start that its stop ended; its details say what it had moved by then."""

    reason = "pre_launch_sync_stopped"


def stopped_refusal(*, uploaded: int = 0, downloaded: int = 0) -> PreLaunchSyncStopped:
    """The refusal of a sync before a start that its stop ended after moving *uploaded* and *downloaded* files."""
    return PreLaunchSyncStopped(
        PRE_LAUNCH_SYNC_STOPPED, synced=uploaded + downloaded, uploaded=uploaded, downloaded=downloaded
    )


class PreLaunchRun:
    """One call of the sync before a start: what it is doing, and its stop."""

    def __init__(self) -> None:
        self.stop = SyncStop()
        self.state = PreLaunchState.WAITING_FOR_SYNC
        self._waiting: asyncio.Timeout | None = None

    async def wait[T](self, state: PreLaunchState, acquire: Callable[[], Awaitable[T]]) -> T:
        """Await what *acquire* starts while answering *state*; a stop before or during it raises :class:`SyncStopped`.

        *acquire* is called only once the stop has been checked, so a call
        stopped before its wait starts leaves nothing unawaited. The wait sits
        in a timeout with no deadline of its own, which the stop expires at
        once — one way to end a wait from outside without the bookkeeping of a
        cancellation that could reach the run after it got what it waited for.
        """
        if self.stop.stops_here():
            raise SyncStopped
        self.state = state
        try:
            async with asyncio.timeout(None) as waiting:
                self._waiting = waiting
                try:
                    return await acquire()
                finally:
                    self._waiting = None
        except TimeoutError as exc:
            if waiting.expired():
                self.stop.stops_here()
                raise SyncStopped from exc
            raise

    def request_stop(self) -> None:
        """Ask this call to stop: at once while it waits, at its next check while it syncs."""
        self.stop.request()
        # A second stop before the expired wait has resumed finds it expiring,
        # which asyncio refuses to reschedule; the first already ends it.
        if self._waiting is not None and not self._waiting.expired():
            self._waiting.reschedule(0)


class PreLaunchRuns:
    """The calls of the sync before a start under way, by ROM.

    A ROM can have more than one: a start given up leaves its sync running,
    and the next start of the same game calls again.
    """

    def __init__(self) -> None:
        self._runs: dict[int, list[PreLaunchRun]] = {}

    @contextmanager
    def track(self, rom_id: int) -> Iterator[PreLaunchRun]:
        """Register a call for *rom_id* for as long as the body runs."""
        run = PreLaunchRun()
        self._runs.setdefault(rom_id, []).append(run)
        try:
            yield run
        finally:
            runs = self._runs[rom_id]
            runs.remove(run)
            if not runs:
                del self._runs[rom_id]

    def state(self, rom_id: int, holder: GateHolder | None) -> dict[str, Any]:
        """What the latest call for *rom_id* is doing, with the trigger it waits for when it waits for the gate.

        A call still waiting while nothing holds the gate has just been handed
        it, and is answered as syncing.
        """
        runs = self._runs.get(rom_id)
        if not runs:
            return {"state": PreLaunchState.IDLE.value}
        state = runs[-1].state
        if state is PreLaunchState.WAITING_FOR_SYNC:
            if holder is None:
                return {"state": PreLaunchState.SYNCING.value}
            return {"state": state.value, "holder": holder.value}
        return {"state": state.value}

    def stop(self, rom_id: int) -> bool:
        """Ask every call for *rom_id* to stop; whether there was one."""
        runs = self._runs.get(rom_id, [])
        for run in runs:
            run.request_stop()
        return bool(runs)
