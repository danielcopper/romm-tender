"""PruneLeaseService — the frontend's hold on the leases it was handed.

A lease holds off a removed-game cleanup across Steam writes the frontend makes
after a call or an event has answered (GLOSSARY.md → Prune conflicts). The
frontend renews the leases it holds, releases each once its writes are done,
and on mount disowns every lease an earlier frontend context left behind.

None of the three checks a conflict rule: they change no local game data, only
the record of claims, and the prune rule would refuse them while a cleanup
runs — when the frontend may still renew or release the lease a run's final
``prune_complete`` carries, or disown what an earlier context held after a
remount.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from services.protocols import ConflictRules


@dataclass(frozen=True)
class PruneLeaseServiceConfig:
    """Frozen wiring bundle handed to ``PruneLeaseService.__init__``: the ``ConflictRules`` the leases are held in."""

    conflict_rules: ConflictRules


class PruneLeaseService:
    """Renew, release and disown the prune leases the frontend holds."""

    def __init__(self, *, config: PruneLeaseServiceConfig) -> None:
        self._rules = config.conflict_rules

    async def release_prune_conflict_lease(self, lease_token: object) -> dict[str, Any]:
        """Release the lease *lease_token* names; an unknown or expired token changes nothing."""
        await self._rules.release_lease(str(lease_token))
        return {"success": True, "message": "Operation lease released."}

    async def renew_prune_conflict_lease(self, lease_token: object) -> dict[str, Any]:
        """Extend the live lease *lease_token* names; a lease that has expired is never revived."""
        renewed = await self._rules.renew_lease(str(lease_token))
        if not renewed:
            return {
                "success": False,
                "reason": "stale_lease",
                "message": "Operation lease is no longer active.",
            }
        return {"success": True, "message": "Operation lease renewed."}

    async def release_orphaned_prune_leases(self) -> dict[str, Any]:
        """Disown every lease a frontend context that is gone left behind, and answer how many.

        Called by a frontend that has just mounted: the context before it can
        no longer release or renew what it held.
        """
        released = await self._rules.release_orphaned_leases()
        return {"success": True, "released": released}
