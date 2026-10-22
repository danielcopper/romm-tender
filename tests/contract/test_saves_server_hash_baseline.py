"""Contract tests for the server-hash baseline identity check (#1468), at the wire.

Drives the real ``Endpoints`` over the real ``bootstrap`` with a registered
device, which is how every sync reaches RomM. Both identity routes sit in the
decision's never-touched branch, and RomM never sends that branch a registered
device: for a save the device has no sync row on, ``GET /api/saves`` answers a
placeholder entry that is not current. So the two cases below land on the
not-current rows instead:

- **Provenance**: a file with sync history whose stored server hash
  (``last_sync_server_hash``) matches the head's ``content_hash`` while the
  local parity hash does not — the head is downloaded (row 10).
- **Parity**: a fresh-install / copied-SD-card file with NO sync history whose
  bytes are byte-identical to the head — the head is downloaded too (row 11a).

Either download first moves the local file into ``.romm-backup``.
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


def _backed_up_contents(local_path: str) -> list[bytes]:
    backup_dir = os.path.join(os.path.dirname(local_path), ".romm-backup")
    stem = os.path.splitext(os.path.basename(local_path))[0]
    contents = []
    for name in sorted(os.listdir(backup_dir)):
        if name.startswith(stem):
            with open(os.path.join(backup_dir, name), "rb") as fh:
                contents.append(fh.read())
    return contents


async def test_provenance_syncs_cleanly_under_scheme_drift(harness):
    """A synced file whose local parity hash mismatches the head's ``content_hash``
    downloads the head, with no conflict, keeping the local copy in ``.romm-backup``.

    The server head carries a ``content_hash`` (``drifted-server-hash``) that
    does NOT equal the local file's real content hash — the exact situation a
    change in RomM's hashing would produce — and we stored that server hash at
    the last sync, with the local file unchanged since. This device has no sync
    row on the head, so RomM's placeholder reads it as not current: row 10
    downloads the head, and the provenance route is not consulted.
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
    assert files["game.srm"]["status"] == "download"
    assert status["conflicts"] == []

    result = await harness.endpoints.sync_rom_saves(42)
    assert result["success"] is True
    assert result["synced"] == 1
    assert result["conflicts"] == []
    assert result["errors"] == []

    assert not any(c[0] == "upload_save" for c in harness.romm.call_log)
    downloads = [c for c in harness.romm.call_log if c[0] == "download_save_content"]
    assert [c[1][0] for c in downloads] == [100]
    assert _backed_up_contents(local_path) == [content]


async def test_parity_match_on_a_fresh_install_downloads_the_head(harness):
    """A fresh-install byte-identical file with NO sync history downloads the head once.

    No baseline, so no stored server hash, and no sync row for this device on
    the head, which RomM's placeholder reads as not current. Row 11a matches the
    parity ``local_hash == server.content_hash`` and downloads the head: never a
    duplicate POST, and the local copy goes to ``.romm-backup`` first.
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
    assert result["synced"] == 1
    assert result["conflicts"] == []
    assert result["errors"] == []
    assert not any(c[0] == "upload_save" for c in harness.romm.call_log)
    downloads = [c for c in harness.romm.call_log if c[0] == "download_save_content"]
    assert [c[1][0] for c in downloads] == [100]
    assert _backed_up_contents(local_path) == [content]
