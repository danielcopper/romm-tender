"""The preview's collapse of the whole library, brought to what the apply's units each collapse.

:class:`~services.library.sync_preview.SyncPreview` reads
every unit into one union and collapses it once; the apply collapses one unit
at a time, and a later unit sees what an earlier one committed. Where the two
would differ for a reason the union cannot see, the correction belongs here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    # ``domain.sync_diff.collapse_sibling_groups`` with everything but the entries bound.
    CollapseFn = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


def collapse_counting_platform_rebinds(
    shortcuts_data: list[dict[str, Any]],
    collapse: CollapseFn,
    *,
    skipped_rom_ids: Collection[int],
    platform_rom_ids: Collection[int],
) -> tuple[list[dict[str, Any]], frozenset[int]]:
    """``(emitted, skipped_rom_ids)``: *shortcuts_data* collapsed, and the ids still to classify as skipped.

    A row a collection skip rebuilt is one of *skipped_rom_ids* outside
    *platform_rom_ids* — a platform unit adds every row it returns to the
    latter, skipped or fetched. Where a row in *platform_rom_ids* carries the
    same ``sibling_group_key``, the apply's platform unit collapses that group
    without the rebuilt row, so *shortcuts_data* is collapsed a second time
    without such rows. An entry that pass keys by one of them is the same-dump
    rebind the platform unit makes: it replaces the first pass's entry, and its
    id is no longer skipped. A rebuilt row the second pass emits nothing for
    keeps its first-pass entry and stays skipped. The second pass runs only
    when such a row exists.
    """
    emitted = collapse(shortcuts_data)
    platform_keys = {sd.get("sibling_group_key") for sd in shortcuts_data if sd["rom_id"] in platform_rom_ids}
    rebuilt = {
        sd["rom_id"]
        for sd in shortcuts_data
        if sd["rom_id"] in skipped_rom_ids
        and sd["rom_id"] not in platform_rom_ids
        and sd.get("sibling_group_key") in platform_keys - {None}
    }
    if not rebuilt:
        return emitted, frozenset(skipped_rom_ids)
    without = collapse([sd for sd in shortcuts_data if sd["rom_id"] not in rebuilt])
    rebound = rebuilt & {entry["rom_id"] for entry in without}
    kept = rebuilt - rebound
    return [*without, *(entry for entry in emitted if entry["rom_id"] in kept)], frozenset(skipped_rom_ids) - rebound
