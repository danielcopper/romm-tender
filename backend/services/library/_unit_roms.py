"""What the preview and the apply both do with a unit's fetched ROMs.

:class:`~services.library.sync_preview.SyncPreview` reads every unit into one
union and :class:`~services.library.unit_apply.UnitApplier` one unit at a time,
and both record a collection unit's membership under the same key and stamp
each fresh ROM's component sibling-group key the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.sibling_group import compute_component_group_keys
from services.library._state import CollectionMembership

if TYPE_CHECKING:
    from domain.work_unit import WorkUnit


def collection_membership_key(unit: WorkUnit) -> tuple[str, str]:
    """The collision-free accumulator key for a collection unit.

    ``(collection_kind, collection_id)`` — the same per-collection identity the
    #742 ``CollectionSyncState`` stamp keys off — so two collections that share a
    display NAME never collide in the finalize accumulator. The single builder
    keeps the real-sync and preview write paths (and the stamp read-back) on one
    key so they cannot drift (#1503).
    """
    return (str(unit.collection_kind), str(unit.id))


def record_collection_membership(
    memberships: dict[tuple[str, str], CollectionMembership], unit: WorkUnit, rom_ids: list[int]
) -> None:
    """Record *rom_ids* as collection *unit*'s full membership; an empty one records nothing."""
    if rom_ids:
        memberships[collection_membership_key(unit)] = CollectionMembership(
            name=unit.name,
            rom_ids=rom_ids,
            kind=str(unit.collection_kind),
            virtual_type=unit.virtual_type,
        )


def stamp_component_group_keys(roms: list[dict[str, Any]], resident_keys: dict[int, str]) -> None:
    """Stamp each fresh ROM's component sibling-group key onto its raw dict.

    Delegates the whole decision to :func:`compute_component_group_keys` (the
    pure kernel: union-find over ``sibling_roms`` edges + canonical-source
    agreement) and writes the result back so the downstream
    :func:`build_shortcuts_data` / group collapse read the component key rather
    than a per-ROM coalesce-first key. A resident dict (one that already carries
    a key — an incremental-reconstructed row) is left untouched: the kernel
    returns no key for it. Mutates *roms* in place, matching the other in-place
    decorations (``platform_name``, popped ``files``) the fetch already applied.
    """
    keys = compute_component_group_keys(roms, resident_keys)
    for rom in roms:
        key = keys.get(int(rom["id"]))
        if key is not None:
            rom["sibling_group_key"] = key
