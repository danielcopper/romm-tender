"""One row of the collections listing the panel reads (``get_collections``).

Shaped from one item of a RomM collection listing — standard, smart or virtual,
which all carry ``id``, ``name``, ``rom_count`` and ``rom_ids`` — plus what only
this device knows about it: whether its sync is enabled, and how many of its
members are in Steam (CONTEXT.md → Reachable).
"""

from __future__ import annotations

from typing import Any


def collection_entry(
    listing: dict[str, Any],
    kind: str,
    enabled: dict[str, dict[str, bool]],
    reachable: set[int] | None,
    **fields: Any,
) -> dict[str, Any]:
    """The listing row for *listing*: what it derives from the item, with the caller's *fields* merged in.

    ``in_steam_count`` counts the member ids in *reachable*, so two versions of
    one game both count although they share one shortcut. It is absent, never
    ``0``, when *reachable* is ``None`` — nothing was established, and a zero
    would claim that none of the collection is in Steam.
    """
    cid = str(listing["id"])
    member_ids = listing.get("rom_ids", [])
    entry = {
        "id": cid,
        "name": listing.get("name", ""),
        "rom_count": listing.get("rom_count", len(member_ids)),
        "sync_enabled": enabled[kind].get(cid, False),
        "kind": kind,
        **fields,
    }
    if reachable is not None:
        entry["in_steam_count"] = sum(1 for rom_id in member_ids if rom_id in reachable)
    return entry
