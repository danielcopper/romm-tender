"""The keys of stored rows a sync does not fetch, as the run reads them and as its commit writes them.

:func:`domain.sibling_group.compute_group_keys_with_unfetched` answers which
rows those are and the key each follows.
:class:`~services.library.sync_preview.SyncPreview` and
:class:`~services.library.unit_apply.UnitApplier` lay the keys over the bound-row
registry they read before they converge and collapse it, so a row is grouped by
the key its commit records; :class:`~services.library.reporter.SyncReporter`
writes them in each chunk's Unit of Work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from services.protocols import UnitOfWork


def with_followed_keys(
    registry: dict[str, dict[str, Any]], followed_keys: Mapping[int, str]
) -> dict[str, dict[str, Any]]:
    """*registry* with each row *followed_keys* names carrying the key it follows; the rest as they are."""
    return {
        rid: {**reg, "sibling_group_key": followed_keys[int(rid)]} if int(rid) in followed_keys else reg
        for rid, reg in registry.items()
    }


def record_followed_keys(uow: UnitOfWork, followed_keys: Mapping[int, str]) -> None:
    """Give each stored row *followed_keys* names the key it follows, in the open write *uow*.

    Every chunk's commit of a unit calls it with the unit's staged keys, a late
    ack's included, so the first commit that writes a member's new key writes
    these too: a row written only after its game's members would no longer be
    followed by the next sync. Writing a key the row already holds changes
    nothing. A row no longer stored is skipped.
    """
    for rom_id, key in followed_keys.items():
        rom = uow.roms.get(rom_id)
        if rom is not None:
            rom.follow_group(key)
            uow.roms.save(rom)
