"""Contract tests for the destructive save callables — ``delete_slot`` and ``resolve_sync_conflict``.

Driven frontend-shaped per ``frontend/src/api/backend.ts``: ``deleteSlot`` is
``callable<[number, string], …>`` and ``resolveSyncConflict`` is
``callable<[number, string, number, "keep_local" | "use_server"], …>``.

``delete_slot`` deletes a slot's server saves and its stored state, never a
file on disk: the files there are the active slot's, and the active slot is
not deletable. It asks RomM for the slot's saves before it writes the stored
state. ``resolve_sync_conflict`` fetches the server head before it writes
either side of the conflict or the ROM's save state; only following a moved
save directory comes earlier. So a server that cannot be reached must come
back as the canonical ``{success: False, reason, message}`` failure with
neither the slot nor either side of the conflict changed. The legacy-bucket
refusal of ``delete_slot("")`` is pinned in ``test_saves_slot_choice.py``, and
the ``keep_local`` upload in ``test_saves_upload_409.py``.
"""

from __future__ import annotations

import hashlib
import os

import pytest

from domain.rom_save_sync_state import RomSaveSyncState
from lib.errors import RommConnectionError
from lib.list_result import ErrorCode

from ._seed import enable_save_sync, seed_install, seed_save_state, seed_server_save


def _write_local_save(harness, *, system: str = "gba", filename: str = "game.srm", content: bytes = b"x") -> str:
    """Materialize a local save file under the harness saves tree."""
    saves_dir = os.path.join(harness.plugin._retrodeck_paths.saves_path(), system)
    os.makedirs(saves_dir, exist_ok=True)
    path = os.path.join(saves_dir, filename)
    with open(path, "wb") as fh:
        fh.write(content)
    return path


def _two_slot_state() -> RomSaveSyncState:
    """``main`` confirmed and active, ``other`` a server slot beside it — the one ``delete_slot`` may take."""
    state = RomSaveSyncState(system="gba")
    state.confirm_slot("main")
    state.refresh_slot_listing(
        {
            "main": {"source": "server", "count": 1, "latest_updated_at": "2026-01-01T00:00:00Z"},
            "other": {"source": "server", "count": 1, "latest_updated_at": "2026-01-01T00:00:00Z"},
        }
    )
    return state


def _assert_canonical_unreachable(result) -> None:
    assert set(result.keys()) == {"success", "reason", "message"}
    assert result["success"] is False
    assert result["reason"] == ErrorCode.SERVER_UNREACHABLE.value
    assert isinstance(result["message"], str)
    assert result["message"]


# ── delete_slot ───────────────────────────────────────────────────────────


async def test_delete_slot_deletes_only_the_slots_server_saves_and_leaves_local_files(harness):
    """A non-active slot's server saves go; the files on disk are the active slot's and stay as they were.

    The active slot cannot be deleted, so the slot being deleted has no files
    of its own on disk: nothing is removed locally and nothing is backed up.
    """
    enable_save_sync(harness)
    seed_install(harness, 42, system="gba", file_name="game.gba")
    local_path = _write_local_save(harness, content=b"main progress")
    state = _two_slot_state()
    state.adopt_baseline("game.srm", tracked_save_id=500, last_sync_hash=hashlib.md5(b"main progress").hexdigest())
    seed_save_state(harness, 42, state)
    seed_server_save(harness, save_id=500, rom_id=42, slot="main", file_name="game.srm")
    seed_server_save(harness, save_id=700, rom_id=42, slot="other", file_name="game.srm")
    seed_server_save(harness, save_id=701, rom_id=42, slot="other", file_name="game.rtc")

    result = await harness.plugin.delete_slot(42, "other")

    assert result == {"success": True, "deleted_server_saves": 2, "cleaned_files": 0}
    deletes = [c[1][0] for c in harness.romm.call_log if c[0] == "delete_server_saves"]
    assert [sorted(ids) for ids in deletes] == [[700, 701]]
    assert set(harness.romm.saves) == {500}
    with open(local_path, "rb") as fh:
        assert fh.read() == b"main progress"
    assert not os.path.exists(os.path.join(os.path.dirname(local_path), ".romm-backup"))
    with harness.uow_factory() as uow:
        after = uow.rom_save_sync_states.get(42)
    assert after is not None
    assert set(after.slots) == {"main"}
    assert after.active_slot == "main"
    assert after.files["game.srm"].tracked_save_id == 500


async def test_delete_slot_server_unreachable_keeps_the_slot(harness):
    """The server delete fails → the canonical failure, and the slot is still there to retry.

    The failure is injected on the delete itself, after the slot's saves were
    listed: the last point at which a half-done delete could still leave the
    slot gone locally while its saves stay on the server.
    """
    enable_save_sync(harness)
    seed_install(harness, 42, system="gba", file_name="game.gba")
    seed_save_state(harness, 42, _two_slot_state())
    seed_server_save(harness, save_id=700, rom_id=42, slot="other", file_name="game.srm")
    harness.romm.delete_server_saves_side_effect = RommConnectionError("offline")

    result = await harness.plugin.delete_slot(42, "other")

    _assert_canonical_unreachable(result)
    assert 700 in harness.romm.saves
    with harness.uow_factory() as uow:
        state = uow.rom_save_sync_states.get(42)
    assert state is not None
    assert set(state.slots) == {"main", "other"}
    assert state.active_slot == "main"


# ── resolve_sync_conflict ─────────────────────────────────────────────────


@pytest.mark.parametrize("action", ["keep_local", "use_server"])
async def test_resolve_sync_conflict_server_unreachable_changes_nothing(harness, action):
    """The server head cannot be fetched → the canonical failure, for either side the user picked.

    Neither side is written: the local file keeps its bytes and nothing is
    uploaded or downloaded.
    """
    enable_save_sync(harness)
    seed_install(harness, 42, system="gba", file_name="game.gba")
    local_path = _write_local_save(harness, content=b"local progress")
    seed_server_save(harness, save_id=100, rom_id=42, slot="default", file_name="game.srm")
    seed_save_state(harness, 42, RomSaveSyncState(active_slot="default", system="gba"))
    harness.romm.list_saves_side_effect = RommConnectionError("offline")

    result = await harness.plugin.resolve_sync_conflict(42, "game.srm", 100, action)

    _assert_canonical_unreachable(result)
    with open(local_path, "rb") as fh:
        assert fh.read() == b"local progress"
    assert not any(c[0] in ("upload_save", "download_save_content") for c in harness.romm.call_log)
