"""Contract test: adopting a file another version of the same game is recorded at.

A sibling group whose installed version lost its shortcut binding to a new id
of the same dump (same ``fs_name``) leaves that file at exactly the path a
download of the bound version would write. The game page offers to use it, and
"Use These Files" adopts it for the bound version. The file is the user's only
copy, so the adoption must keep it and record it as the bound version's install.

Drives the real ``RomAdoptionService`` → sibling supersede → ``RomRemovalService``
wiring over real SQLite and real file stores under ``tmp_path``; only RomM's
transport is the fake.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

import pytest

from ._seed import seed_group_member

# Every download, adoption and removal here lands in RetroDECK's folders.
pytestmark = pytest.mark.usefixtures("seeded_retrodeck")

_GROUP = "igdb:900:gba"
_OLD_ID = 41
_NEW_ID = 42
_APP_ID = 700
_FS_NAME = "game.gba"
_DATA = b"the only copy of the dump"


def _stage_server_rom(harness, rom_id: int, *, fs_name: str) -> None:
    harness.romm.roms[rom_id] = {
        "id": rom_id,
        "name": fs_name,
        "platform_slug": "gba",
        "fs_name": fs_name,
        "fs_size_bytes": len(_DATA),
        "has_file_on_disk": True,
        "files": [
            {
                "file_name": fs_name,
                "file_size_bytes": len(_DATA),
                "md5_hash": hashlib.md5(_DATA, usedforsecurity=False).hexdigest(),
            }
        ],
    }


async def _drain_background_tasks() -> None:
    """Await the fire-and-forget download task(s) `start_download` spawned."""
    for _ in range(6):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def _seed_rebound_group(harness, *, new_fs_name: str) -> Path:
    """The old version installed and unbound; the new one bound and not installed."""
    old_path = seed_group_member(
        harness,
        _OLD_ID,
        group_key=_GROUP,
        shortcut_app_id=None,
        name=_FS_NAME,
        installed=True,
        file_name=_FS_NAME,
    )
    assert old_path is not None
    Path(old_path).parent.mkdir(parents=True, exist_ok=True)
    Path(old_path).write_bytes(_DATA)
    seed_group_member(harness, _NEW_ID, group_key=_GROUP, shortcut_app_id=_APP_ID, name=new_fs_name)
    _stage_server_rom(harness, _NEW_ID, fs_name=new_fs_name)
    return Path(old_path)


async def test_using_the_files_another_version_is_recorded_at_keeps_them_and_installs_the_bound_version(harness):
    path = _seed_rebound_group(harness, new_fs_name=_FS_NAME)

    page = await harness.endpoints.get_cached_game_detail(_APP_ID)
    pressed = await harness.endpoints.start_download(_NEW_ID, False)
    result = await harness.endpoints.adopt_existing_rom(_NEW_ID)

    assert page["installed"] is False
    assert page["target_path_occupied"] is True
    assert pressed["reason"] == "target_occupied"
    assert pressed["existing"]["path"] == str(path)
    assert pressed["adoptable"] is True
    assert path.read_bytes() == _DATA
    assert result["success"] is True
    installed = harness.endpoints.get_installed_rom(_NEW_ID)
    assert installed is not None
    assert installed["file_path"] == str(path)
    assert harness.endpoints.get_installed_rom(_OLD_ID) is None
    with harness.uow_factory() as uow:
        assert uow.rom_installs.get(_OLD_ID) is None
        new_rom = uow.roms.get(_NEW_ID)
    assert new_rom is not None
    assert result["app_id"] == _APP_ID
    assert result["launch_options"]
    assert new_rom.applied_launch_options == result["launch_options"]


async def test_control_a_bound_version_with_its_own_file_name_adopts_its_own_file(harness):
    # The same group with the new version under another name: its own file sits
    # at its own path, the old version's elsewhere, so the supersede and the
    # adoption touch different files.
    old_path = _seed_rebound_group(harness, new_fs_name="game-new.gba")
    new_path = old_path.parent / "game-new.gba"
    new_path.write_bytes(_DATA)

    result = await harness.endpoints.adopt_existing_rom(_NEW_ID)

    assert result["success"] is True
    assert new_path.read_bytes() == _DATA
    installed = harness.endpoints.get_installed_rom(_NEW_ID)
    assert installed is not None
    assert installed["file_path"] == str(new_path)
    assert harness.endpoints.get_installed_rom(_OLD_ID) is None
    assert not old_path.exists()


async def test_a_download_that_replaces_the_file_another_version_is_recorded_at_still_supersedes_it(harness, caplog):
    # Only an adoption keeps what is at its path. Replace is the user's choice to
    # put RomM's copy there, so the version recorded at it is superseded as before.
    path = _seed_rebound_group(harness, new_fs_name=_FS_NAME)
    harness.romm.download_payloads[f"rom:{_NEW_ID}:{_FS_NAME}"] = b"RomM's copy"

    with caplog.at_level(logging.INFO):
        result = await harness.endpoints.start_download(_NEW_ID, True)
        await _drain_background_tasks()

    assert result["success"] is True
    assert harness.endpoints.get_installed_rom(_OLD_ID) is None
    installed = harness.endpoints.get_installed_rom(_NEW_ID)
    assert installed is not None
    assert installed["file_path"] == str(path)
    assert path.read_bytes() == b"RomM's copy"
    supersede_lines = [r.getMessage() for r in caplog.records if f"rom {_OLD_ID}" in r.getMessage()]
    assert supersede_lines == [f"Superseding install of rom {_OLD_ID} (group of rom {_NEW_ID})"]


async def test_a_record_refused_after_the_hand_over_leaves_the_file_to_adopt_again(harness, monkeypatch):
    # The hand-over drops the old record before the new one is written. A record
    # refused in between leaves neither version installed, the file untouched,
    # and the page offering the same file again — and the second press records it.
    path = _seed_rebound_group(harness, new_fs_name=_FS_NAME)
    recorder = harness.app.services.rom_adoption_service._install_recorder
    monkeypatch.setattr(recorder, "do_record_install", lambda **_: (None, "Invalid install metadata: refused"))

    refused = await harness.endpoints.adopt_existing_rom(_NEW_ID)

    assert refused["success"] is False
    assert refused["reason"] == "invalid_install"
    assert path.read_bytes() == _DATA
    assert harness.endpoints.get_installed_rom(_OLD_ID) is None
    assert harness.endpoints.get_installed_rom(_NEW_ID) is None
    page = await harness.endpoints.get_cached_game_detail(_APP_ID)
    assert page["target_path_occupied"] is True

    monkeypatch.undo()
    again = await harness.endpoints.adopt_existing_rom(_NEW_ID)

    assert again["success"] is True
    installed = harness.endpoints.get_installed_rom(_NEW_ID)
    assert installed is not None
    assert installed["file_path"] == str(path)
    assert path.read_bytes() == _DATA
