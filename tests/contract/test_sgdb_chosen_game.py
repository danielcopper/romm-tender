"""Contract tests for the SteamGridDB game a user chooses where RomM gives none.

Drives ``apply_sgdb_game_id`` and ``get_sgdb_resolution`` over the real wired
backend, positionally and typed as ``frontend/src/api/backend.ts`` declares
them: the pick is stored in the real ``roms`` row, the next resolution resolves
to it without the name search, an id RomM supplies later wins, and a sync's
upsert of the row leaves the choice alone.
"""

from __future__ import annotations

import pytest

from ._seed import seed_rom

_ROM_ID = 41
_PICKED = 8888


@pytest.fixture
def picked(harness):
    """Rom 41 with no SteamGridDB id from RomM, a key, and game 8888's four images on SteamGridDB."""
    harness.settings["steamgriddb_api_key"] = "fake-sgdb-key"
    seed_rom(harness, _ROM_ID)
    harness.romm.roms[_ROM_ID] = {"id": _ROM_ID, "name": "Obscure Port"}
    for asset_type, image_id in (("hero", 801), ("logo", 802), ("grid", 803), ("icon", 804)):
        url = f"https://cdn.example/{image_id}.png"
        harness.sgdb.seed_artwork(_PICKED, asset_type, url, image_id)
        harness.sgdb.seed_image_bytes(url, asset_type.encode())


def _ids(harness) -> tuple[int | None, int | None]:
    with harness.uow_factory() as uow:
        rom = uow.roms.get(_ROM_ID)
    return rom.sgdb_id, rom.chosen_sgdb_id


async def test_a_picked_game_is_stored_and_the_next_resolution_does_not_ask_again(harness, picked):
    assert await harness.endpoints.apply_sgdb_game_id(_ROM_ID, _PICKED) == {"success": True}
    searched = len(harness.sgdb.requested_paths)

    resolution = await harness.endpoints.get_sgdb_resolution(_ROM_ID)

    assert resolution == {"decision": "resolved", "sgdb_id": _PICKED}
    assert _ids(harness) == (None, _PICKED)
    assert harness.sgdb.requested_paths[searched:] == []


async def test_an_id_romm_supplies_later_wins(harness, picked):
    await harness.endpoints.apply_sgdb_game_id(_ROM_ID, _PICKED)
    harness.romm.roms[_ROM_ID] = {"id": _ROM_ID, "name": "Obscure Port", "sgdb_id": 7777}

    resolution = await harness.endpoints.get_sgdb_resolution(_ROM_ID)

    assert resolution == {"decision": "resolved", "sgdb_id": 7777}
    assert _ids(harness) == (7777, _PICKED)


async def test_a_resync_of_the_row_keeps_the_choice(harness, picked):
    await harness.endpoints.apply_sgdb_game_id(_ROM_ID, _PICKED)

    seed_rom(harness, _ROM_ID)

    assert _ids(harness) == (None, _PICKED)
