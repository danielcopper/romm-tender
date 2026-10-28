"""Pure builders for the file-status DTO shape consumed by the saves UI.

Anything that derives status metadata from a sync action, the local
file state, and the server-side save records — without touching disk,
state, or network — belongs here. Code that needs the live
:class:`RomSaveSyncState` aggregate, network calls, or file I/O belongs in
``services/saves/status/``.
"""

from __future__ import annotations

from typing import Any

from domain.iso_time import parse_iso_to_epoch
from domain.sync_action import Conflict, Download, Skip, Upload


def build_file_status(
    filename: str,
    *,
    local_path: str | None,
    local_hash: str | None,
    local_mtime: str | None,
    local_size: int | None,
    server: dict[str, Any] | None,
    last_sync_at: str | None,
    status: str,
    server_device_id: str | None = None,
    uploaded_by_us: bool | None = None,
) -> dict[str, Any]:
    """Build a file status dict for the frontend."""
    server_device_syncs = server.get("device_syncs", []) if server else []
    device_syncs = [
        {
            "device_id": ds.get("device_id", ""),
            "device_name": ds.get("device_name", ""),
            "is_current": ds.get("is_current", False),
            "last_synced_at": ds.get("last_synced_at"),
        }
        for ds in (shown_device_syncs(server, server_device_id) if server else [])
    ]
    own_sync = (
        next(
            (ds for ds in server_device_syncs if ds.get("device_id") == server_device_id),
            None,
        )
        if server_device_id
        else None
    )
    is_current = own_sync.get("is_current", True) if own_sync else True

    return {
        "filename": filename,
        "local_path": local_path,
        "local_hash": local_hash,
        "local_mtime": local_mtime,
        "local_size": local_size,
        "server_save_id": server.get("id") if server else None,
        "server_file_name": server.get("file_name") if server else None,
        "server_emulator": server.get("emulator") if server else None,
        "server_updated_at": server.get("updated_at", "") if server else None,
        "server_size": server.get("file_size_bytes") if server else None,
        "last_sync_at": last_sync_at,
        "status": status,
        "device_syncs": device_syncs,
        "is_current": is_current,
        "uploaded_by_us": uploaded_by_us,
    }


def shown_device_syncs(save: dict[str, Any], device_id: str | None) -> list[dict[str, Any]]:
    """The *save*'s ``device_syncs`` without RomM's placeholder for *device_id*.

    The placeholder is the entry naming *device_id* with ``is_current`` false
    while its ``last_synced_at`` is at or after the save's ``updated_at``; what
    it is, and why only the panel's copy drops it:
    ``docs/architecture/save-file-sync-architecture.md``, "GET /api/saves
    writes no sync row". Every other entry is kept as RomM sent it, and so is
    one whose times cannot be read.
    """
    device_syncs: list[dict[str, Any]] = save.get("device_syncs") or []
    if not device_id:
        return list(device_syncs)
    updated_at = parse_iso_to_epoch(save.get("updated_at"))

    def is_placeholder(ds: dict[str, Any]) -> bool:
        if ds.get("device_id") != device_id or ds.get("is_current") is not False or updated_at is None:
            return False
        synced_at = parse_iso_to_epoch(ds.get("last_synced_at"))
        return synced_at is not None and synced_at >= updated_at

    return [ds for ds in device_syncs if not is_placeholder(ds)]


def status_from_action(action: object) -> str:
    """Map a ``SyncAction`` outcome to the legacy file-status string."""
    if isinstance(action, Skip):
        return "synced"
    if isinstance(action, Upload):
        return "upload"
    if isinstance(action, Download):
        return "download"
    if isinstance(action, Conflict):
        return "conflict"
    return "synced"


def resolve_chosen_server(action: object, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the server-save dict to display alongside the file-status row.

    - ``Download`` and ``Conflict`` carry the chosen save explicitly on the
      action — use that.
    - ``Skip`` falls back to the newest in *candidates* so the status panel
      still shows a server reference where one exists (e.g. synced rows
      continue to display the server save's metadata).
    - ``Upload(target_save_id=None)`` (POST-as-new) has no server reference
      yet → ``None``.
    - ``Upload(target_save_id=int)`` echoes the existing save it supersedes
      (status display only — every upload POSTs; the id no longer selects an
      HTTP verb) — fall back to the newest in *candidates* so the status panel
      still shows the server-side metadata while the upload is pending.
    """
    if isinstance(action, Download | Conflict):
        return action.server_save
    if isinstance(action, Upload) and action.target_save_id is None:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda s: parse_iso_to_epoch(s.get("updated_at")) or 0.0)
