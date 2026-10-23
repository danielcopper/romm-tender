"""RomSaveSyncState — per-ROM save-sync state for one tracked ROM.

The active slot and whether the user confirmed it, the emulator/system the ROM
runs under, the last core synced, our upload attribution, the merged slot
listing the UI reads, and the per-file sync baselines the newest-wins matrix
uses to detect drift. References its Rom by id (the registry key). The merge
logic that produces the slot listing lives in a service; this aggregate accepts
the result and guards the slot/file invariants. Tracks save *files* (SRAM) only
— the savestate twin, if ever built, is RomSavestateSyncState (see
docs/architecture/database-design.md, "Reserved naming").

Invariants enforced here:

1. A tracked file (an entry in ``files``) always carries a non-empty hash
   baseline. :meth:`adopt_baseline`, the only way a file is added, additionally
   requires a server save id; a stored baseline without one is a hash-only
   adopt an earlier version recorded.
2. A non-legacy active slot always has its key present in ``slots`` (the legacy
   ``None`` slot uses the ``""`` key).
3. ``own_upload_ids`` never grows by mutating ``None`` — :meth:`track_own_upload`
   starts a list when attribution was previously unknown.

``FileSyncState`` is the immutable per-file value object the aggregate builds
whole; it has no behaviour of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from domain._aggregate import cosmic_aggregate


@dataclass(frozen=True, slots=True)
class FileSyncState:
    """Per-file sync baseline — last-observed hashes, sizes, and timestamps.

    Immutable value object owned by :class:`RomSaveSyncState`; the aggregate builds
    one whole on :meth:`RomSaveSyncState.adopt_baseline` so the newest-wins matrix
    can detect drift against it on the next sync.

    ``last_sync_hash`` is our own content hash of the local file at the last
    sync (the drift baseline). ``last_sync_server_hash`` is the server-provided
    ``content_hash`` of that same sync — RomM's own digest of the bytes, stored
    so an identity-vs-server check can compare two server-produced hashes
    instead of relying on our local reimplementation staying byte-for-byte
    identical to RomM's scheme (#1468). It is ``None`` where the server save
    carried no hash, and for a baseline recorded before this field existed or by
    an earlier version's hash-only adopt; the identity check falls back to
    parity there. The two hashes are always recorded together at a single sync
    event — never re-paired independently — so a stored server hash truthfully
    corresponds to its ``last_sync_hash``.

    ``last_sync_content_size`` is the local file's content size at that sync
    (:mod:`domain.save_measure`), ``None`` where it was not taken — a baseline
    recorded before it existed, or one recorded from the server's size alone.
    """

    tracked_save_id: int | None = None
    last_sync_hash: str | None = None
    last_sync_server_hash: str | None = None
    last_sync_at: str = ""
    last_sync_server_updated_at: str = ""
    last_sync_server_save_id: int | None = None
    last_sync_server_size: int | None = None
    last_sync_local_mtime: float | None = None
    last_sync_local_size: int | None = None
    last_sync_content_size: int | None = None


@cosmic_aggregate
class RomSaveSyncState:
    """Save-sync state for one ROM — slot config, attribution, per-file baselines."""

    active_slot: str | None = None
    slot_confirmed: bool = False
    emulator: str = "retroarch"
    system: str = ""
    last_synced_core: str | None = None
    # ``None`` means "uploader attribution unknown / legacy"; ``[]`` means "we
    # definitely uploaded nothing". Both are meaningful — the distinction lets
    # the UI hide the attribution badge for legacy entries instead of asserting
    # "not yours".
    own_upload_ids: list[int] | None = None
    slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    files: dict[str, FileSyncState] = field(default_factory=dict)
    last_sync_check_at: str | None = None

    def adopt_baseline(
        self,
        filename: str,
        *,
        tracked_save_id: int,
        last_sync_hash: str,
        last_sync_server_hash: str | None = None,
        last_sync_at: str = "",
        last_sync_server_updated_at: str = "",
        last_sync_server_save_id: int | None = None,
        last_sync_server_size: int | None = None,
        last_sync_local_mtime: float | None = None,
        last_sync_local_size: int | None = None,
        last_sync_content_size: int | None = None,
    ) -> None:
        """Record ``filename``'s sync baseline, replacing any existing entry.

        The only way to add a file to ``files`` — enforces that every tracked
        file carries both a server save id and a hash baseline (invariant 1).
        Re-calling with an existing filename re-adopts the baseline under the
        known ``tracked_save_id``. ``last_sync_server_hash`` is the server's own
        ``content_hash`` for this sync (``None`` when the response/save carried
        none — the identity check then falls back to parity, #1468); it is paired
        with ``last_sync_hash`` here so the two always describe the same sync
        event. Raises ``ValueError`` if the id is not positive or the hash is
        empty.
        """
        if tracked_save_id <= 0:
            raise ValueError("tracked_save_id must be positive")
        if not last_sync_hash:
            raise ValueError("last_sync_hash is required to adopt a baseline")
        self.files[filename] = FileSyncState(
            tracked_save_id=tracked_save_id,
            last_sync_hash=last_sync_hash,
            last_sync_server_hash=last_sync_server_hash,
            last_sync_at=last_sync_at,
            last_sync_server_updated_at=last_sync_server_updated_at,
            last_sync_server_save_id=last_sync_server_save_id,
            last_sync_server_size=last_sync_server_size,
            last_sync_local_mtime=last_sync_local_mtime,
            last_sync_local_size=last_sync_local_size,
            last_sync_content_size=last_sync_content_size,
        )

    def backfill_content_size(self, filename: str, *, local_hash: str, content_size: int) -> None:
        """Record the content size of ``filename``'s baseline, taken from the file still holding it.

        Applies only where the baseline records no content size and the local
        file is unchanged since it (``local_hash`` equals ``last_sync_hash``):
        those are the bytes the baseline was recorded from, so their content
        size is the one that sync would have recorded. Anything else is a no-op.
        """
        existing = self.files.get(filename)
        if existing is None or existing.last_sync_content_size is not None:
            return
        if not local_hash or local_hash != existing.last_sync_hash:
            return
        self.files[filename] = replace(existing, last_sync_content_size=content_size)

    def track_own_upload(self, save_id: int) -> None:
        """Attribute ``save_id`` to an upload we made (idempotent).

        Starts the attribution list when it was previously unknown (``None``)
        rather than mutating ``None`` (invariant 3). Already-tracked ids are
        ignored.
        """
        if self.own_upload_ids is None:
            self.own_upload_ids = [save_id]
        elif save_id not in self.own_upload_ids:
            self.own_upload_ids.append(save_id)

    def confirm_slot(self, name: str) -> None:
        """Confirm ``name`` as the user-chosen active slot.

        Marks the slot confirmed and ensures its key exists in ``slots``. A slot
        must carry a non-empty name: the legacy ``slot:null`` confirmation is
        retired (#1276), so an empty or ``None`` name raises ``ValueError``. The
        legacy no-slot mode survives only as a migration *source* (via
        :meth:`switch_active_slot` / direct construction), never as a confirmed
        target.
        """
        if not name:
            raise ValueError(
                "confirm_slot requires a non-empty slot name — legacy slot:null confirmation is retired (#1276)"
            )
        self.active_slot = name
        self.slot_confirmed = True
        self.slots.setdefault(name, {"source": "local", "count": 0, "latest_updated_at": None})

    def switch_active_slot(self, name: str | None) -> None:
        """Switch the active slot to ``name`` without confirming it.

        Same empty-string normalization and slots-key guarantee as
        :meth:`confirm_slot`, but leaves ``slot_confirmed`` untouched — a switch
        is not a confirmation.
        """
        normalized = name or None
        self.active_slot = normalized
        self.slots.setdefault(normalized or "", {"source": "local", "count": 0, "latest_updated_at": None})

    def promote_slot_to_server(self, slot: str) -> None:
        """Mark a local-only ``slot`` as having a server copy after an upload.

        Flips the slot's ``source`` marker from ``local`` to ``server`` and seeds
        its count at 1 — the state after a local-only slot's first save reaches
        the server. A no-op when ``slot`` is untracked or already server-sourced,
        so re-running an upload never double-counts. Raises ``ValueError`` if the
        slot name is empty.
        """
        if not slot:
            raise ValueError("slot is required")
        entry = self.slots.get(slot)
        if entry is not None and entry.get("source") == "local":
            entry["source"] = "server"
            entry["count"] = 1

    def mark_sync_evaluated(self, at: str) -> None:
        """Record that the sync matrix was last evaluated at ISO timestamp ``at``."""
        self.last_sync_check_at = at

    def adopt_system(self, system: str) -> None:
        """Record the emulator system this ROM runs under.

        Stamped on the first sync that observes the ROM's system (the
        aggregate ships with an empty ``system``). A no-op when ``system`` is
        empty so a missing system never clobbers a previously-known one.
        """
        if system:
            self.system = system

    def record_synced_core(self, core: str | None, emulator: str) -> None:
        """Stamp the emulator and (optionally) the core the last sync ran under.

        ``emulator`` is always recorded and must be non-empty — a sync always
        runs under some emulator tag. ``core`` is optional: pass ``None`` to
        record only the emulator without clobbering a previously-known core
        (the emulator-only update case). Raises ``ValueError`` on an empty
        ``emulator``.
        """
        if not emulator:
            raise ValueError("emulator is required")
        self.emulator = emulator
        if core is not None:
            self.last_synced_core = core

    def refresh_slot_listing(self, merged: dict[str, dict[str, Any]]) -> None:
        """Replace the slot listing with the service-computed ``merged`` view."""
        self.slots = merged

    def delete_file_tracking(self, filename: str) -> None:
        """Drop ``filename``'s per-file baseline, so the next sync decides the file as one with no baseline.

        Idempotent: a no-op when ``filename`` is not tracked.
        """
        self.files.pop(filename, None)

    def delete_slot_tracking(self, slot: str) -> None:
        """Drop ``slot`` from the slot listing (the slot was deleted).

        Idempotent: a no-op when ``slot`` is not present.
        """
        self.slots.pop(slot, None)

    def clear_baselines(self) -> None:
        """Drop all per-file baselines (the active slot changed, invalidating them)."""
        self.files = {}
