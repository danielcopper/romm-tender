"""Contract tests for the server-hash baseline identity check (#1468), at the wire.

Drives the real ``Endpoints`` over the real ``bootstrap`` with a registered
device, as the sync entry points reach RomM. The provenance route can match
where the local file is unchanged since its baseline and the identity check
runs first: the never-touched branch (row 6d) and the ``is_current=false``
branch (row 10a). RomM never sends a registered device into the never-touched
branch: for a save the device has no sync row on, ``GET /api/saves`` answers a
placeholder entry that is not current. The parity route also decides rows 8a,
11a and 12a. So the two cases below land on the not-current rows:

- **Provenance**: a file with sync history whose stored server hash
  (``last_sync_server_hash``) matches the head's ``content_hash`` while the
  local parity hash does not — the head is adopted (row 10a).
- **Parity**: a fresh-install / copied-SD-card file with NO sync history whose
  bytes are byte-identical to the head — the head is adopted too (row 11a).

Either adopt moves no bytes: the head is acknowledged to RomM and recorded as
the file's baseline, and the local file stays where it is.
"""

from __future__ import annotations

import hashlib
import os

from domain.rom_save_sync_state import FileSyncState, RomSaveSyncState

from ._seed import enable_save_sync, seed_install, seed_save_state, seed_server_save


def _write_local_save(harness, *, system: str, content: bytes, filename: str) -> str:
    saves_dir = os.path.join(harness.saves_root, system)
    os.makedirs(saves_dir, exist_ok=True)
    path = os.path.join(saves_dir, filename)
    with open(path, "wb") as fh:
        fh.write(content)
    return path


def _baseline(harness) -> FileSyncState:
    with harness.uow_factory() as uow:
        state = uow.rom_save_sync_states.get(42)
    assert state is not None
    return state.files["game.srm"]


def _nothing_moved_aside(local_path: str) -> bool:
    return not os.path.exists(os.path.join(os.path.dirname(local_path), ".romm-backup"))


async def test_a_stored_server_hash_under_scheme_drift_adopts_the_head(harness):
    """A synced file whose local parity hash mismatches the head's ``content_hash``
    adopts the head, with no conflict and no transfer.

    The server head carries a ``content_hash`` (``drifted-server-hash``) that
    does NOT equal the local file's real content hash — the exact situation a
    change in RomM's hashing would produce — and we stored that server hash at
    the last sync, with the local file unchanged since. This device has no sync
    row on the head, so RomM's placeholder reads it as not current: the
    provenance route proves the bytes identical, and row 10a adopts the head and
    acknowledges it.
    """
    enable_save_sync(harness)
    seed_install(harness, 42, system="gba", file_name="game.gba")
    content = b"unchanged local save bytes"
    local_path = _write_local_save(harness, system="gba", content=content, filename="game.srm")
    local_hash = hashlib.md5(content).hexdigest()

    drifted_server_hash = "drifted-server-hash"
    assert drifted_server_hash != local_hash  # parity would fail

    # Sync history: our baseline matches the current local (unchanged) and stores
    # the server's own hash from that sync.
    state = RomSaveSyncState(
        active_slot="default",
        slot_confirmed=True,
        system="gba",
        files={
            "game.srm": FileSyncState(
                tracked_save_id=100,
                last_sync_hash=local_hash,
                last_sync_server_hash=drifted_server_hash,
            )
        },
    )
    seed_save_state(harness, 42, state)

    # The server head: same slot, no sync row for us, and a content_hash equal to
    # our stored server hash but NOT the local parity hash. Same bytes as ours.
    entry = seed_server_save(harness, save_id=100, rom_id=42, slot="default", file_name="game.srm")
    entry["content_hash"] = drifted_server_hash
    harness.romm.set_server_save_content(100, content)

    status = await harness.endpoints.get_save_status(42)
    files = {f["filename"]: f for f in status["files"]}
    assert files["game.srm"]["status"] == "synced"
    assert status["conflicts"] == []

    result = await harness.endpoints.sync_rom_saves(42)
    assert result["success"] is True
    assert result["synced"] == 0
    assert result["conflicts"] == []
    assert result["errors"] == []

    assert not any(c[0] in ("upload_save", "download_save_content") for c in harness.romm.call_log)
    assert [c[1][0] for c in harness.romm.call_log if c[0] == "confirm_download"] == [100]
    assert _nothing_moved_aside(local_path)
    baseline = _baseline(harness)
    assert (baseline.tracked_save_id, baseline.last_sync_server_hash) == (100, drifted_server_hash)


async def test_parity_match_on_a_fresh_install_adopts_the_head(harness):
    """A fresh-install byte-identical file with NO sync history adopts the head.

    No baseline, so no stored server hash, and no sync row for this device on
    the head, which RomM's placeholder reads as not current. Row 11a matches the
    parity ``local_hash == server.content_hash`` and adopts the head with no
    transfer: never a duplicate POST, never the same bytes downloaded again, and
    the head acknowledged to RomM.
    """
    enable_save_sync(harness)
    seed_install(harness, 42, system="gba", file_name="game.gba")
    content = b"fresh install identical bytes"
    local_path = _write_local_save(harness, system="gba", content=content, filename="game.srm")
    local_hash = hashlib.md5(content).hexdigest()

    # Confirmed slot, but no per-file baseline (never synced this device).
    seed_save_state(harness, 42, RomSaveSyncState(active_slot="default", slot_confirmed=True, system="gba"))

    entry = seed_server_save(harness, save_id=100, rom_id=42, slot="default", file_name="game.srm")
    entry["content_hash"] = local_hash  # byte-identical → parity route
    harness.romm.set_server_save_content(100, content)

    result = await harness.endpoints.sync_rom_saves(42)
    assert result["success"] is True
    assert result["synced"] == 0
    assert result["conflicts"] == []
    assert result["errors"] == []
    assert not any(c[0] in ("upload_save", "download_save_content") for c in harness.romm.call_log)
    assert [c[1][0] for c in harness.romm.call_log if c[0] == "confirm_download"] == [100]
    assert _nothing_moved_aside(local_path)
    baseline = _baseline(harness)
    assert (baseline.tracked_save_id, baseline.last_sync_hash) == (100, local_hash)
