"""The conflict rules a use case checks at its entry (CONTEXT.md → Conflict rules).

A refused endpoint answers with the canonical refusal the matching gate
decorator gives, from the same home in ``lib/migration_gate.py``,
``lib/sync_gate.py`` and ``lib/prune_gate.py``.

A use case names the endpoint it serves as the label, the same label the
decorator takes from the method's name, so the prune gate names the holder in
its log lines either way. The rule-coverage tests (``tests/_gate_rules.py``)
read each ``hold`` call's label and rule keywords from the source, so both are
written as literals.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from lib.migration_gate import migration_refusal
from lib.prune_gate import prune_active_refusal
from lib.sync_gate import sync_refusal

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from lib.prune_gate import PruneConflicts


class ConflictRuleSet:
    """Checks the conflict rules a use case names, in their pinned order, and takes and gives back its leases."""

    def __init__(
        self,
        *,
        prune_conflicts: PruneConflicts,
        migration_pending: Callable[[], bool],
        sync_in_flight: Callable[[], bool],
    ) -> None:
        self._prune_conflicts = prune_conflicts
        self._migration_pending = migration_pending
        self._sync_in_flight = sync_in_flight

    @contextlib.asynccontextmanager
    async def hold(
        self, label: str, *, migration: bool = False, sync: bool = False, prune: bool = False
    ) -> AsyncGenerator[dict[str, Any] | None]:
        """Check the named rules for *label* and hold what they need for the block.

        Yields the refusal of the first named rule that holds, or ``None`` when
        the block may run.
        A refused call registers nothing. With ``prune`` the block runs under an
        operation named *label*, registered in the same lock hold as the check
        and released when the block ends, however it ends.
        """
        if migration and self._migration_pending():
            yield migration_refusal()
            return
        if sync and self._sync_in_flight():
            yield sync_refusal()
            return
        if not prune:
            yield None
            return
        registration = await self._prune_conflicts.hold_operation(label)
        if registration is None:
            yield prune_active_refusal()
            return
        try:
            yield None
        finally:
            await self._prune_conflicts.release_operation(registration)

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
