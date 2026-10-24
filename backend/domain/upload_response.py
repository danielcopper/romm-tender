"""What RomM's answer to a save upload says beyond the save it names.

Pure reads of the ``add_save`` / ``update_save`` response, made by the save
transfer before it records the upload.
"""

from __future__ import annotations

from typing import Any

from domain.iso_time import parse_iso_to_epoch


def dedup_returned_non_head(response: dict[str, Any], planned_group: list[dict[str, Any]]) -> bool:
    """Whether an ``add_save`` POST deduped to a pre-existing NON-head save (#1482).

    RomM's ``add_save`` content-dedup can early-return an EXISTING save whose
    content matches the upload instead of creating a new version. When that
    returned save is not the slot head the run planned against — an older save
    while a newer, different head still leads the slot — the upload intent (make
    our content the slot head) went silently unfulfilled: no new version, the
    foreign head stays authoritative, yet the response looks like a successful
    upload. Recording it as a synced baseline would falsely report "synced".

    Detected purely from ids against *planned_group* (the pre-upload
    ``list_saves`` snapshot for this file, the same group ``compute_sync_action``
    evaluated): the response is a member of that snapshot (an existing save, not
    a freshly minted version whose id is absent from it) that is not the newest
    save in it. Benign — today's baseline write stands — for an empty planned
    slot (no head to bypass), a freshly created version, or the head coming back.
    """
    response_id = response.get("id")
    if response_id is None or not planned_group:
        return False
    head = max(planned_group, key=lambda s: parse_iso_to_epoch(s.get("updated_at")) or 0.0)
    group_ids = {s.get("id") for s in planned_group}
    return response_id in group_ids and response_id != head.get("id")


def upload_response_proves_current(response: dict[str, Any], device_id: str) -> bool:
    """Whether *response* proves *device_id* already holds the current save.

    Reads the same ``device_syncs`` shape ``domain.sync_action`` reads. Fail-open:
    only an entry for *device_id* whose ``is_current`` is exactly ``True`` counts;
    any missing / non-list / otherwise-unexpected shape returns ``False`` so the
    caller still confirms.
    """
    device_syncs = response.get("device_syncs")
    if not isinstance(device_syncs, list):
        return False
    return any(
        isinstance(ds, dict) and ds.get("device_id") == device_id and ds.get("is_current") is True
        for ds in device_syncs
    )
