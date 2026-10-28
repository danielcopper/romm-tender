"""Whether RomM's ``add_save`` would refuse a new save into a slot — pure compute, stdlib only.

RomM's ``backend/endpoints/saves.py::add_save`` (5.3.0 through 5.4.1), for a
POST with a device, a slot and ``overwrite=false``, takes the slot's saves
newest ``updated_at`` first, ties by ``Save.id`` descending (``_saves_query``),
and answers 409 unless the device's sync row on the first of them has a
``last_synced_at`` at or after its ``updated_at``. The listing's
``device_syncs`` entry for the calling device carries exactly that comparison
as ``is_current`` (``_build_save_schema``), and a placeholder with
``is_current`` false where the device has no row, so the rule can be read off a
listing taken with this device's id.
"""

from __future__ import annotations

from typing import Any

from domain.iso_time import parse_iso_to_epoch
from domain.save_slot import save_in_slot
from domain.upload_response import upload_response_proves_current


def slot_refuses_new_save(server_saves: list[dict[str, Any]], slot: str, device_id: str) -> bool | None:
    """Whether RomM would refuse a new save from *device_id* into *slot*, read off *server_saves*.

    ``False`` for a slot that holds no save. Otherwise the slot's newest save,
    over every file name, decides: ``False`` when *device_id*'s entry on it has
    ``is_current`` exactly ``True``, ``True`` for any other entry, a missing
    one, or a ``device_syncs`` of another shape. ``None`` when the newest save
    cannot be established — a save in the slot whose ``updated_at`` does not
    parse, or a tie on the newest time where a tied save has no integer ``id``.
    """
    in_slot = [s for s in server_saves if save_in_slot(s, slot)]
    if not in_slot:
        return False
    newest = _newest_save(in_slot)
    if newest is None:
        return None
    return not upload_response_proves_current(newest, device_id)


def _newest_save(saves: list[dict[str, Any]]) -> dict[str, Any] | None:
    times = [parse_iso_to_epoch(s.get("updated_at")) for s in saves]
    readable = [t for t in times if t is not None]
    if len(readable) != len(times):
        return None
    latest = max(readable)
    tied = [s for s, t in zip(saves, readable, strict=True) if t == latest]
    if len(tied) == 1:
        return tied[0]
    if not all(isinstance(s.get("id"), int) for s in tied):
        return None
    return max(tied, key=lambda s: s["id"])
