"""The conflict rules a use case checks at its entry (GLOSSARY.md → Conflict rules), and the refusals they answer with.

A use case names the endpoint it serves as the label, so the prune conflicts
name the holder in their log lines. The rule-coverage tests
(``tests/_conflict_rules.py``) read each ``hold`` and ``hold_start`` call's label
and rule keywords from the source, so both are written as literals.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from lib.errors import Refused

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from lib.prune_conflicts import PruneConflicts

_UPDATE_MESSAGE = "Tender is installing an update and will restart in a moment."
_MIGRATION_MESSAGE = "Pending RetroDECK migration. Open the Tender menu (QAM) to migrate or dismiss."
_SYNC_MESSAGE = "A library sync is in progress — wait for it to finish or cancel it first."
_PRUNE_ACTIVE_MESSAGE = "A removed-game cleanup is in progress; wait for it to finish before changing local game data."


def update_refused(**details: Any) -> Refused:
    """The refusal of a use case while an update of this program is being installed, *details* beside it."""
    return Refused("blocked_by_update", _UPDATE_MESSAGE, **details)


def migration_refused(**details: Any) -> Refused:
    """The refusal of a use case while a RetroDECK migration is pending, *details* beside it."""
    return Refused("blocked_by_migration", _MIGRATION_MESSAGE, **details)


def update_refusal() -> dict[str, Any]:
    """The canonical answer of an endpoint refused while an update of this program is being installed."""
    return {"success": False, "reason": "blocked_by_update", "message": _UPDATE_MESSAGE}


def migration_refusal() -> dict[str, Any]:
    """The canonical answer of an endpoint refused while a RetroDECK migration is pending."""
    return {"success": False, "reason": "blocked_by_migration", "message": _MIGRATION_MESSAGE}


def sync_refusal() -> dict[str, Any]:
    """The canonical answer of an endpoint refused while a library sync is in flight."""
    return {"success": False, "reason": "sync_active", "message": _SYNC_MESSAGE}


def prune_active_refusal() -> dict[str, Any]:
    """The canonical answer of an endpoint refused while a removed-game cleanup is running."""
    return {"success": False, "reason": "prune_active", "message": _PRUNE_ACTIVE_MESSAGE}


def operation_active_refusal(message: str) -> dict[str, Any]:
    """The answer of a cleanup start refused while an operation or a lease is held; *message* names the holder."""
    return {"success": False, "reason": "operation_active", "message": message}


class ConflictRuleSet:
    """Checks the conflict rules a use case names, in their pinned order, and takes and gives back its leases."""

    def __init__(
        self,
        *,
        prune_conflicts: PruneConflicts,
        update_in_progress: Callable[[], bool],
        migration_pending: Callable[[], bool],
        sync_in_flight: Callable[[], bool],
    ) -> None:
        self._prune_conflicts = prune_conflicts
        self._update_in_progress = update_in_progress
        self._migration_pending = migration_pending
        self._sync_in_flight = sync_in_flight

    @contextlib.asynccontextmanager
    async def hold(
        self,
        label: str,
        *,
        update: bool = False,
        migration: bool = False,
        sync: bool = False,
        prune: bool = False,
    ) -> AsyncGenerator[dict[str, Any] | None]:
        """Check the named rules for *label* and hold what they need for the block.

        Yields the refusal of the first named rule that holds, or ``None`` when
        the block may run.
        A refused call registers nothing. With ``prune`` the block runs under an
        operation named *label*, registered in the same lock hold as the check
        and released when the block ends, however it ends. The update rule is
        asked once more after the registration: an install pressed while it
        waited for the lock saw no operation, and would otherwise stop this
        process under the block.
        """
        refusal = self._first_refusal(update=update, migration=migration, sync=sync)
        if refusal is not None:
            yield refusal
            return
        if not prune:
            yield None
            return
        registration = await self._prune_conflicts.hold_operation(label)
        if registration is None:
            yield prune_active_refusal()
            return
        if update and self._update_in_progress():
            await self._prune_conflicts.release_operation(registration)
            yield update_refusal()
            return
        try:
            yield None
        finally:
            await self._prune_conflicts.release_operation(registration)

    @contextlib.asynccontextmanager
    async def hold_start(
        self, label: str, *, update: bool = False, migration: bool = False, sync: bool = False
    ) -> AsyncGenerator[dict[str, Any] | None]:
        """Reserve a cleanup's exclusive start for *label*, then check the named rules, and hold the reservation.

        Yields the refusal of the first rule that holds, or ``None`` when the
        block may run. The reservation is taken first: it refuses every
        conflicting endpoint from that moment on, so a sync cannot start
        between the sync rule's answer and the reservation. A reservation
        refused while an operation or a lease is held answers
        ``operation_active``; an update, migration or sync refusal gives the
        reservation back before it answers, so a refused start leaves no claim,
        and so does a rule that raises. Otherwise the reservation is given back
        when the block ends, however it ends.
        """
        message = await self._prune_conflicts.reserve_start(label)
        if message is not None:
            yield operation_active_refusal(message)
            return
        try:
            refusal = self._first_refusal(update=update, migration=migration, sync=sync)
        except BaseException:
            self._prune_conflicts.release_reservation()
            raise
        if refusal is not None:
            self._prune_conflicts.release_reservation()
            yield refusal
            return
        try:
            yield None
        finally:
            self._prune_conflicts.release_reservation()

    def _first_refusal(self, *, update: bool, migration: bool, sync: bool) -> dict[str, Any] | None:
        if update and self._update_in_progress():
            return update_refusal()
        if migration and self._migration_pending():
            return migration_refusal()
        if sync and self._sync_in_flight():
            return sync_refusal()
        return None

    async def retain(self, task: asyncio.Task[Any], label: str) -> None:
        """Hold an operation named *label* until *task* ends — detached work a use case started.

        Checks no rule: call it inside the ``hold(..., prune=True)`` block that
        started *task*, so the operation is registered before that block's own
        is released and no cleanup can start in between.
        """
        await self._prune_conflicts.retain(task, label)

    async def acquire_lease(self, key: str) -> str:
        """Take a lease under *key* for Steam writes the frontend makes after the call or the event; answer its token.

        Checks no rule, so call it where no cleanup can start before the lease
        is held: inside the ``hold(..., prune=True)`` block of the call whose
        answer carries the token, or, for an event, while something else still
        refuses a cleanup's start — the library sync emits ``sync_complete``
        and ``sync_stale`` before its run ends, and the sync rule refuses
        ``start_prune`` until then.
        """
        return await self._prune_conflicts.acquire_lease(key)

    async def release_lease(self, token: str) -> None:
        """Release the lease *token* names; an unknown or expired token changes nothing."""
        await self._prune_conflicts.release_lease(token)

    async def renew_lease(self, token: str) -> bool:
        """Extend the live lease *token* names; ``False`` when it is unknown or has expired."""
        return await self._prune_conflicts.renew_lease(token)

    async def release_orphaned_leases(self) -> int:
        """Drop every lease a frontend holds, and answer how many there were."""
        return await self._prune_conflicts.release_orphaned_leases()

    async def emit_under_lease(self, key: str, emit_with: Callable[[str], Awaitable[bool]]) -> None:
        """Take a lease under *key* and emit through *emit_with*, which puts its token in ``prune_lease_token``.

        For an event whose Steam writes the frontend makes after the backend's
        part is over. Checks no rule, like :meth:`acquire_lease`, so call it
        while something else still refuses a cleanup's start. *emit_with* is
        the emit itself, written at the call site as a literal
        ``self._emit("<event>", …)`` so ``scripts/check_event_parity.py`` sees
        the event. The lease is released again when the emit raises or nobody
        heard the event, since no frontend holds its token then to release it.
        """
        token = await self.acquire_lease(key)
        try:
            heard = await emit_with(token)
        except BaseException:
            await self.release_lease(token)
            raise
        if not heard:
            await self.release_lease(token)
