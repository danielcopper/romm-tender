"""Contract test: an installed version gone from RomM whose dump came back under a new id.

The library sync moves the shortcut onto the same dump (same group, a file on
RomM, the same ``fs_name_no_ext``), installed or not, rather than onto another
version of the game. The new version is then not installed while its file
already sits at its path, so its game page offers Use Existing Files.

Drives the real sync orchestrator, the real commit and the real game-page read
over real SQLite and real file stores under ``tmp_path``; RomM's transport is
the fake, and the frontend's ack is the one stand-in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from domain.sync_run_kind import SyncRunKind

from ._seed import seed_group_member

pytestmark = pytest.mark.usefixtures("seeded_retrodeck")

_PLATFORM_ID = 2
_GROUP = f"igdb:900:{_PLATFORM_ID}"
_GONE_ID = 41
_SAME_DUMP_ID = 42
_OTHER_VERSION_ID = 43
_APP_ID = 700
_FS_NAME = "game.gba"


def _server_rom(rom_id: int, fs_name: str, *, is_main_sibling: bool = False) -> dict[str, Any]:
    stem = fs_name.rsplit(".", 1)[0]
    return {
        "id": rom_id,
        "name": stem,
        "fs_name": fs_name,
        "fs_name_no_ext": stem,
        "igdb_id": 900,
        "platform_id": _PLATFORM_ID,
        "platform_name": "Game Boy Advance",
        "platform_slug": "gba",
        "has_file_on_disk": True,
        "rom_user": {"is_main_sibling": is_main_sibling},
    }


async def test_the_shortcut_follows_the_same_dump_and_its_page_offers_the_installed_file(harness):
    path = seed_group_member(
        harness,
        _GONE_ID,
        group_key=_GROUP,
        shortcut_app_id=_APP_ID,
        name=_FS_NAME,
        installed=True,
        file_name=_FS_NAME,
    )
    assert path is not None
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"the dump")
    harness.romm.platforms = [{"id": _PLATFORM_ID, "name": "Game Boy Advance", "slug": "gba", "rom_count": 2}]
    harness.romm.roms[_SAME_DUMP_ID] = _server_rom(_SAME_DUMP_ID, _FS_NAME)
    # Another version RomM ranks first: the move must not take it.
    harness.romm.roms[_OTHER_VERSION_ID] = _server_rom(_OTHER_VERSION_ID, "game (europe).gba", is_main_sibling=True)
    harness.settings["enabled_platforms"] = {str(_PLATFORM_ID): True}

    sync = harness.app.services.sync_service

    async def ack_reuse(_unit, event):
        event.set()
        return {str(rom_id): _APP_ID for rom_id in sync._box.pending_sync}

    sync._orchestrator._unit_applier._chunk_dispatcher._wait_for_unit_complete = ack_reuse
    assert sync._box.try_begin_run("run-1", kind=SyncRunKind.APPLY) is True
    await sync._orchestrator._do_sync_per_unit()

    with harness.uow_factory() as uow:
        assert uow.roms.get(_SAME_DUMP_ID).shortcut_app_id == _APP_ID
        assert uow.roms.get(_GONE_ID).shortcut_app_id is None
        assert uow.roms.get(_OTHER_VERSION_ID).shortcut_app_id is None
        assert uow.rom_installs.get(_GONE_ID) is not None
    page = await harness.endpoints.get_cached_game_detail(_APP_ID)
    assert page["rom_id"] == _SAME_DUMP_ID
    assert page["installed"] is False
    assert page["target_path_occupied"] is True
    assert Path(path).read_bytes() == b"the dump"
