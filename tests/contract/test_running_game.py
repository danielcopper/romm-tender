"""No save sync changes the save of a game Steam lists as running (#2620), over the real wiring.

The harness's Steam reading lists the running game's shortcut app id; the real
``RunningGames`` matches it to the ROM whose shortcut it is, and the endpoints
are called as the panel calls them.
"""

from __future__ import annotations

import os

from domain.rom_save_sync_state import RomSaveSyncState

from ._seed import enable_save_sync, seed_install, seed_save_state, seed_server_save

_RUNNING = 42
_OTHER = 7


def _local_save(harness, filename: str, content: bytes = b"progress") -> str:
    saves_dir = os.path.join(harness.saves_root, "gba")
    os.makedirs(saves_dir, exist_ok=True)
    path = os.path.join(saves_dir, filename)
    with open(path, "wb") as fh:
        fh.write(content)
    return path


def _two_games(harness) -> tuple[str, str]:
    """ROM 42 (running, bound to app id 42) and ROM 7, each confirmed with a local save; returns their paths."""
    enable_save_sync(harness)
    seed_install(harness, _RUNNING, file_name="game.gba")
    seed_install(harness, _OTHER, file_name="other.gba")
    for rom_id in (_RUNNING, _OTHER):
        seed_save_state(harness, rom_id, RomSaveSyncState(active_slot="main", slot_confirmed=True, system="gba"))
    harness.steam.listed_app_ids = (_RUNNING,)
    return _local_save(harness, "game.srm"), _local_save(harness, "other.srm")


async def test_sync_all_saves_leaves_the_running_game_alone_and_names_it(harness):
    running_save, other_save = _two_games(harness)
    seed_server_save(harness, save_id=100, rom_id=_RUNNING, file_name="game.srm")

    result = await harness.endpoints.sync_all_saves()

    with open(running_save, "rb") as fh:
        assert fh.read() == b"progress"
    assert not any(call[0] == "list_saves" and call[1][0] == _RUNNING for call in harness.romm.call_log)
    assert any(call[0] == "upload_save" and call[1][0] == _OTHER for call in harness.romm.call_log)
    assert os.path.exists(other_save)
    assert result["success"] is True
    assert result["skipped_running"] == [{"rom_id": _RUNNING, "name": "rom-42"}]
    assert result["message"].endswith(". rom-42 was skipped because it is running; its saves sync when you quit it")


async def test_sync_saves_on_the_running_game_answers_game_running_naming_it(harness):
    _two_games(harness)

    result = await harness.endpoints.sync_rom_saves(_RUNNING)

    assert result["success"] is False
    assert result["reason"] == "game_running"
    assert result["message"] == "rom-42 is running. Quit it first; its saves sync when you quit it."
    assert result["synced"] == 0
    assert not any(call[0] in ("list_saves", "upload_save") for call in harness.romm.call_log)


async def test_sync_saves_on_another_game_is_not_refused(harness):
    _two_games(harness)

    result = await harness.endpoints.sync_rom_saves(_OTHER)

    assert result["success"] is True


async def test_a_platforms_delete_keeps_the_running_game_s_saves_and_names_it(harness):
    running_save, other_save = _two_games(harness)

    result = await harness.endpoints.delete_platform_saves("gba")

    assert os.path.exists(running_save)
    assert not os.path.exists(other_save)
    assert result["success"] is True
    assert result["skipped_running"] == [{"rom_id": _RUNNING, "name": "rom-42"}]
    assert result["message"] == "Deleted 1 save file(s) from 1 ROM(s). rom-42 was skipped because it is running"


async def test_with_no_steam_reading_nothing_counts_as_running(harness):
    _two_games(harness)
    # Steam lists the game, but no reading of the list could be taken.
    harness.steam.apps = None

    result = await harness.endpoints.sync_rom_saves(_RUNNING)

    assert result["success"] is True
