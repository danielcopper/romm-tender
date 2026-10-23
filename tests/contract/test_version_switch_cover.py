"""Contract tests for the cover a version switch leaves behind (#2381 D28).

Drives ``switch_version`` and the cover calls the frontend makes after it —
``get_sgdb_artwork_base64(rom_id, 0)``, ``fetch_cover_base64``, and, for a
version with neither a chosen cover nor RomM's, ``get_sgdb_slot_artwork`` with
``replace`` and its report — positionally, over the real wired backend and a
real grid directory, then reads the ARTWORK tab: a switch never leaves the cover
reading "Set in Steam" over a record of Tender's SteamGridDB cover it no longer
shows.
"""

from __future__ import annotations

import base64
import hashlib

from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_outcome import ArtworkSlotOutcome
from domain.rom import Rom

_GROUP = "igdb:100:57"
_APP_ID = 42


def _seed(harness):
    harness.settings["steamgriddb_api_key"] = "fake-sgdb-key"
    with harness.uow_factory() as uow:
        for rom_id, app_id in ((1, _APP_ID), (2, None)):
            uow.roms.save(
                Rom(
                    rom_id=rom_id,
                    platform_slug="snes",
                    name=f"Game {rom_id}",
                    fs_name=f"game_{rom_id}.sfc",
                    shortcut_app_id=app_id,
                    last_synced_at="2026-01-01T00:00:00",
                    sibling_group_key=_GROUP,
                    sgdb_id=9999,
                )
            )
        # Version 2 showed Tender's SteamGridDB cover while it was bound.
        uow.artwork_slot_outcomes.save(
            ArtworkSlotOutcome.record_set(
                rom_id=2,
                slot=ArtworkSlot.COVER,
                sgdb_id=9999,
                image_id=5,
                image_sha256=hashlib.sha256(b"version 2's steamgriddb cover").hexdigest(),
                recorded_at=1.0,
            )
        )
    harness.romm.roms[1] = {"id": 1, "sibling_roms": [{"id": 2}]}
    grid = harness.tmp_path / "home" / ".local" / "share" / "Steam" / "userdata" / "123" / "config" / "grid"
    grid.mkdir(parents=True)
    # Version 1, bound now, shows RomM's cover.
    (grid / f"{_APP_ID}p.png").write_bytes(b"version 1's romm cover")
    return grid


async def _switch_and_publish(harness, grid) -> None:
    """Switch to version 2 and write the cover as ``publishCommittedVersionSwitch`` does."""
    switched = await harness.endpoints.switch_version(_APP_ID, 2, False)
    await harness.endpoints.release_prune_conflict_lease(switched["prune_lease_token"])
    chosen = await harness.endpoints.get_sgdb_artwork_base64(2, 0)
    assert chosen["chosen"] is False
    romms = await harness.endpoints.fetch_cover_base64(2)
    if romms["base64"]:
        (grid / f"{_APP_ID}p.png").write_bytes(base64.b64decode(romms["base64"]))
        return
    served = await harness.endpoints.get_sgdb_slot_artwork(2, _APP_ID, "cover", True)
    if served.get("status") != "image":
        return
    await harness.endpoints.release_prune_conflict_lease(served["prune_lease_token"])
    (grid / f"{_APP_ID}p.png").write_bytes(base64.b64decode(served["base64"]))
    await harness.endpoints.report_sgdb_slot_artwork(
        2, "cover", "set", served["sgdb_id"], served["image_id"], served["sha256"]
    )


async def _cover_row(harness):
    slots = await harness.endpoints.get_artwork_slots(2, _APP_ID)
    cover = next(row for row in slots["slots"] if row["slot"] == "cover")
    return cover["state"], cover["source"], cover["image_id"]


async def test_back_to_a_version_romm_has_no_cover_for_it_shows_its_steamgriddb_pick(harness):
    grid = _seed(harness)
    harness.romm.roms[2] = {"id": 2, "name": "Game 2"}
    harness.sgdb.seed_raw_response(
        "/grids/game/9999?dimensions=600x900",
        {"success": True, "data": [{"id": 6, "url": "https://cdn.example/6.png", "width": 600, "height": 900}]},
    )
    harness.sgdb.seed_image_bytes("https://cdn.example/6.png", b"version 2's steamgriddb cover, today")

    await _switch_and_publish(harness, grid)

    assert await _cover_row(harness) == ("automatic", "tender", 6)


async def test_back_to_a_version_with_nothing_to_show_leaves_no_cover_set_in_steam(harness):
    grid = _seed(harness)
    harness.romm.roms[2] = {"id": 2, "name": "Game 2"}
    harness.settings["steamgriddb_api_key"] = ""

    await _switch_and_publish(harness, grid)

    assert (await _cover_row(harness))[0] == "automatic"


async def test_romm_gaining_a_cover_while_another_version_was_bound_shows_romms(harness):
    grid = _seed(harness)
    cover_url = "/assets/romm/resources/roms/2/cover/big.png?ts=1"
    harness.romm.roms[2] = {"id": 2, "name": "Game 2", "path_cover_large": cover_url}
    harness.romm.download_payloads[f"cover:{cover_url}"] = b"version 2's romm cover"

    await _switch_and_publish(harness, grid)

    assert await _cover_row(harness) == ("automatic", "romm", None)
