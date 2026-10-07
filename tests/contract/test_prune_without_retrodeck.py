"""Contract: the removed-game cleanup on a machine where no RetroDECK is detected.

Its own module because ``test_prune.py`` seeds a RetroDECK for every test in
it. Here the resolver detects none, so RetroDECK names no saves root and no ROM
root: the cleanup still removes what needs no folder of RetroDECK's, and a game
whose files or saves lie where nothing bounds them is reported with nothing
touched — never as a run that may have moved something.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from domain.platform_sync_state import PlatformSyncState
from domain.rom import Rom
from domain.rom_install import RomInstall
from domain.rom_save_sync_state import FileSyncState, RomSaveSyncState
from lib.errors import RommNotFoundError

_ROM_ID = 41
_CONTROL_ROM_ID = 900041


def _seed_removed_game(harness) -> None:
    """A gba row the server no longer serves, beside a control row it still does."""
    removed = Rom.synced(
        rom_id=_ROM_ID,
        platform_slug="gba",
        name="Removed Game",
        fs_name="Removed Game.gba",
        shortcut_app_id=None,
        synced_at="2026-01-01T00:00:00",
    )
    removed.record_fetch_generation("older-fetch")
    control = Rom.synced(
        rom_id=_CONTROL_ROM_ID,
        platform_slug="snes",
        name="Kept Game",
        fs_name="Kept Game.sfc",
        shortcut_app_id=None,
        synced_at="2026-01-01T00:00:00",
    )
    control.record_fetch_generation("snes-fetch")
    with harness.uow_factory() as uow:
        uow.roms.save(removed)
        uow.roms.save(control)
        for slug, fetch_id in (("gba", "completed-fetch"), ("snes", "snes-fetch")):
            uow.platform_sync_state.save(
                PlatformSyncState.stamp(platform_slug=slug, at="2026-01-02T00:00:00", rom_count=1, fetch_id=fetch_id)
            )
    harness.romm.get_rom_once_side_effect_by_id[_ROM_ID] = RommNotFoundError("gone")


def _seed_install_with_a_save(harness) -> tuple[Path, Path]:
    """The removed game's files and its save, where a RetroDECK no longer detected left them."""
    rom_path = Path(harness.roms_root) / "gba" / "Removed Game.gba"
    rom_path.parent.mkdir(parents=True)
    rom_path.write_bytes(b"installed rom")
    save_path = Path(harness.saves_root) / "gba" / "Removed Game.srm"
    save_path.parent.mkdir(parents=True)
    save_path.write_bytes(b"local save")
    with harness.uow_factory() as uow:
        uow.rom_installs.save(
            RomInstall.mark_installed(
                rom_id=_ROM_ID,
                file_path=str(rom_path),
                rom_dir=None,
                platform_slug="gba",
                system="gba",
                installed_at="2026-01-01T00:00:00",
            )
        )
        uow.rom_save_sync_states.save(
            _ROM_ID,
            RomSaveSyncState(system="gba", files={"Removed Game.srm": FileSyncState(last_sync_hash="known")}),
        )
    return rom_path, save_path


async def _run_cleanup(harness, *, installed: bool) -> dict[str, Any]:
    """Preview, select the installed content where there is some, run, and answer the run's completion frame."""
    preview = await harness.endpoints.get_prune_preview(
        {"scope": "bulk", "rom_id": None, "preview_id": None, "offset": 0, "limit": 50}
    )
    request: dict[str, object] = {
        "preview_id": preview["preview_id"],
        "confirmed": True,
        "repoint_shortcuts": True,
        "remove_rows": True,
        "remove_fully_vanished": True,
        # Selecting installed content requires a recovery bundle.
        "create_recovery_bundle": installed,
    }
    if installed:
        staged = await harness.endpoints.stage_prune_installed_selection(
            {"preview_id": preview["preview_id"], "selection_id": None, "rom_ids": [_ROM_ID], "final": True}
        )
        request["installed_selection_id"] = staged["selection_id"]
    else:
        request["include_installed_rom_ids"] = []
    started = await harness.endpoints.start_prune(request)
    assert started["success"] is True
    task = harness.app.services.prune_service._task
    assert task is not None
    await task
    return [call.args[1] for call in harness.emit.await_args_list if call.args[0] == "prune_complete"][-1]


async def test_without_retrodeck_a_game_with_nothing_on_disk_is_removed(harness):
    _seed_removed_game(harness)

    complete = await _run_cleanup(harness, installed=False)

    with harness.uow_factory() as uow:
        assert uow.roms.get(_ROM_ID) is None
    assert complete["removed_rom_ids"] == [_ROM_ID]
    result = complete["results"][0]
    assert result["status"] == "removed"
    assert "ambiguous_mutations" not in result


async def test_without_retrodeck_a_game_with_files_and_saves_is_reported_and_nothing_is_touched(harness):
    _seed_removed_game(harness)
    rom_path, save_path = _seed_install_with_a_save(harness)

    complete = await _run_cleanup(harness, installed=True)

    with harness.uow_factory() as uow:
        assert uow.roms.get(_ROM_ID) is not None
        assert uow.rom_installs.get(_ROM_ID) is not None
    assert rom_path.read_bytes() == b"installed rom"
    assert save_path.read_bytes() == b"local save"
    assert not (save_path.parent / ".romm-backup").exists()
    assert complete["removed_rom_ids"] == []
    result = complete["results"][0]
    assert result["rom_ids"] == [_ROM_ID]
    assert result["message"] == "Uninstalling needs RetroDECK, which is not installed."
    assert "ambiguous_mutations" not in result
