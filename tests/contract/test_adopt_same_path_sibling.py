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

import hashlib
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
