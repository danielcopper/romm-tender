"""Device-level single-owner serialization gate for save-sync.

One in-flight save-sync run per device: a second trigger waits (queue
semantics) for the in-flight run to finish rather than running alongside
it. Every trigger but the sync before a start bounds that wait, and on
expiry gets a timeout it can turn into its busy fallthrough; the sync
before a start waits until the gate is free, because whether to keep
waiting is the user's choice at the start. The :class:`asyncio.Lock` IS
the serializer/queue; this module owns the acquire discipline around it
and the record of which trigger holds it.

Run-lifecycle state (run ids, sync-state boxes) and cancellation are out
of scope here.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

__all__ = [
    "POST_EXIT_GATE_TIMEOUT",
    "SYNC_ALL_GATE_TIMEOUT",
    "SYNC_ROM_GATE_TIMEOUT",
    "GateHolder",
    "SaveSyncGate",
    "SaveSyncTimeoutError",
]

# Per-trigger bounded-wait budgets (seconds). The manual full-library sweep
# gets the most slack because it legitimately runs longest.
POST_EXIT_GATE_TIMEOUT: float = 60.0
SYNC_ROM_GATE_TIMEOUT: float = 15.0
SYNC_ALL_GATE_TIMEOUT: float = 60.0


class GateHolder(StrEnum):
    """Which save-sync trigger holds the device gate."""

    PRE_LAUNCH = "pre_launch"
    POST_EXIT = "post_exit"
    ROM_SYNC = "rom_sync"
    SYNC_ALL = "sync_all"


class SaveSyncTimeoutError(Exception):
    """Raised when a save-sync run cannot acquire the device gate in time.

    Signals that another run is in flight and the bounded wait elapsed — the
    caller raises ``SyncBusy`` from it. It is a
    LOCAL scheduling outcome: no caller may report it as a server verdict.
    """


class SaveSyncGate:
    """Admits one save-sync run per device, queuing the rest, and says which trigger holds it.

    A single :class:`asyncio.Lock` serializes every save-sync run on the
    device: the first caller holds it for the duration of its run, later
    callers wait their turn (queue semantics), up to a per-call timeout where
    the caller names one. The gate sits OUTSIDE the per-ROM lock — it is the
    device-wide single-owner seam, not a per-ROM one. It offers no
    cancellation.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._holder: GateHolder | None = None

    @asynccontextmanager
    async def held_by(self, holder: GateHolder, *, max_wait: float | None) -> AsyncIterator[None]:
        """Acquire the gate as :meth:`bounded_run` does, recording *holder* for as long as it is held."""
        async with self.bounded_run(max_wait=max_wait):
            self._holder = holder
            try:
                yield
            finally:
                self._holder = None

    @asynccontextmanager
    async def bounded_run(self, *, max_wait: float | None) -> AsyncIterator[None]:
        """Acquire the device gate within *max_wait* seconds, holding it for the body.

        Waits up to *max_wait* seconds to acquire the gate (queuing behind any
        in-flight run), and without a limit where it is ``None``. On expiry it
        raises :class:`SaveSyncTimeoutError` WITHOUT holding the lock, so a
        timed-out caller never traps the next run. On success the lock is
        released in ``finally`` even if the body raises.
        """
        acquired = False
        try:
            async with asyncio.timeout(max_wait):
                await self._lock.acquire()
                acquired = True
        except TimeoutError as exc:
            # The acquire can win a photo-finish with the deadline; if it did,
            # release here so a timed-out caller never leaks the held lock.
            if acquired:
                self._lock.release()
            raise SaveSyncTimeoutError(f"save-sync gate not acquired within {max_wait}s") from exc
        try:
            yield
        finally:
            self._lock.release()

    def is_in_flight(self) -> bool:
        """Whether a save-sync run currently holds the gate."""
        return self._lock.locked()

    def holder(self) -> GateHolder | None:
        """The trigger holding the gate, or ``None`` while nothing that named itself holds it."""
        return self._holder
