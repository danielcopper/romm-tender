"""Emitting a library-sync event whose Steam writes outlive the run (CONTEXT.md → Prune conflicts).

The frontend applies ``sync_stale`` removals and ``sync_complete``'s collection
writes after the backend's part of the run is over, so each of those events
carries a lease that holds off a removed-game cleanup until the frontend
releases it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from services.protocols import ConflictRules


async def emit_under_lease(rules: ConflictRules, key: str, emit_with: Callable[[str], Awaitable[bool]]) -> None:
    """Take a lease under *key* and emit through *emit_with*, which puts its token in ``prune_lease_token``.

    *emit_with* is the emit itself, written at the call site as a literal
    ``self._emit("<event>", …)`` so ``scripts/check_event_parity.py`` sees the
    event. The lease is released again when the emit raises or nobody heard
    the event, since no frontend holds its token then to release it.
    """
    token = await rules.acquire_lease(key)
    try:
        heard = await emit_with(token)
    except BaseException:
        await rules.release_lease(token)
        raise
    if not heard:
        await rules.release_lease(token)
