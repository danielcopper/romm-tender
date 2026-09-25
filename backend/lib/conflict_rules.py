"""The conflict rules a use case checks at its entry, applied in one order.

Three conditions refuse an operation before it does anything, and they are
asked in this order: a pending RetroDECK migration, a library sync in flight,
and a running removed-game cleanup (CONTEXT.md → Conflict rules). The answer to
a refused call is the canonical refusal the matching gate decorator gives, from
the same home in ``lib/migration_gate.py``, ``lib/sync_gate.py`` and
``lib/prune_gate.py``.

A use case names the endpoint it serves as the label, the same label the
decorator takes from the method's name, so the prune gate names the holder in
its log lines either way. The contract tests read each ``hold`` call's label and
rule keywords from the source, so both are written as literals.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from lib.migration_gate import migration_refusal
from lib.prune_gate import prune_active_refusal
from lib.sync_gate import sync_refusal

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncIterator, Callable

    from lib.prune_gate import PruneConflicts


class ConflictRuleSet:
    """The one place the conflict rules are checked, in their pinned order."""

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
    ) -> AsyncIterator[dict[str, Any] | None]:
        """Check the named rules for *label* and hold what they need for the block.

        Yields the first rule's refusal, or ``None`` when the block may run.
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
        """Hold an operation named *label* until *task* ends — detached work a use case started."""
        await self._prune_conflicts.retain(task, label)
