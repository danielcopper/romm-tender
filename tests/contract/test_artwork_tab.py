"""Contract tests for the game page's ARTWORK tab (#2381, cut 2).

Drives ``get_artwork_slots``, ``get_artwork_slot_candidates``,
``choose_artwork_slot_image``, ``set_artwork_slot_automatic`` and the cover's
``report_sgdb_slot_artwork`` over the real wired backend, positionally and typed
as ``frontend/src/api/backend.ts`` declares them, against a real grid directory
and the real ``artwork_slot_choices`` table.
"""

from __future__ import annotations

import base64
import hashlib

from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_choice import ArtworkSlotChoice

from ._seed import seed_rom

_ROM_ID = 41


def _grid(harness):
    harness.settings["steamgriddb_api_key"] = "fake-sgdb-key"
    seed_rom(harness, _ROM_ID)
    with harness.uow_factory() as uow:
        rom = uow.roms.get(_ROM_ID)
        rom.assign_sgdb_id(9999)
        uow.roms.save(rom)
    path = harness.tmp_path / "home" / ".local" / "share" / "Steam" / "userdata" / "123" / "config" / "grid"
    path.mkdir(parents=True)
    return path


def _seed_logos(harness, *image_ids: int) -> None:
    data = []
    for image_id in image_ids:
        url = f"https://cdn.example/{image_id}.png"
        data.append(
            {"id": image_id, "url": url, "thumb": f"https://cdn.example/t{image_id}.jpg", "width": 800, "height": 300}
        )
        harness.sgdb.seed_image_bytes(url, f"logo {image_id}".encode())
    harness.sgdb.seed_raw_response("/logos/game/9999", {"success": True, "data": data})


def _choice(harness, slot: ArtworkSlot):
    with harness.uow_factory() as uow:
        return uow.artwork_slot_choices.get(_ROM_ID, slot)


def _rows(answer):
    return {row["slot"]: (row["state"], row["source"], row["image_id"]) for row in answer["slots"]}


async def test_a_picked_image_is_served_recorded_and_read_back_as_chosen(harness):
    _grid(harness)
    _seed_logos(harness, 1, 2)

    listed = await harness.endpoints.get_artwork_slot_candidates(_ROM_ID, "logo", 0)
    chosen = await harness.endpoints.choose_artwork_slot_image(_ROM_ID, "logo", 2)
    await harness.endpoints.release_prune_conflict_lease(chosen.pop("prune_lease_token"))
    slots = await harness.endpoints.get_artwork_slots(_ROM_ID, _ROM_ID)

    assert listed["success"] is True
    assert [c["image_id"] for c in listed["candidates"]] == [1, 2]
    assert listed["tenders_pick"] == 1
    assert (chosen["status"], chosen["image_id"], base64.b64decode(chosen["base64"])) == ("image", 2, b"logo 2")
    assert _choice(harness, ArtworkSlot.LOGO).image_id == 2
    assert _rows(slots)["logo"] == ("chosen", "chosen", 2)


async def test_automatic_again_drops_the_choice(harness):
    _grid(harness)
    _seed_logos(harness, 1, 2)
    await harness.endpoints.get_artwork_slot_candidates(_ROM_ID, "logo", 0)
    chosen = await harness.endpoints.choose_artwork_slot_image(_ROM_ID, "logo", 2)
    await harness.endpoints.release_prune_conflict_lease(chosen.pop("prune_lease_token"))

    automatic = await harness.endpoints.set_artwork_slot_automatic(_ROM_ID, "logo")
    await harness.endpoints.release_prune_conflict_lease(automatic.pop("prune_lease_token"))

    assert (automatic["status"], automatic["image_id"]) == ("image", 1)
    assert _choice(harness, ArtworkSlot.LOGO) is None


async def test_a_slot_set_in_steam_reads_so(harness):
    grid = _grid(harness)
    (grid / f"{_ROM_ID}_hero.jpg").write_bytes(b"set by hand")

    slots = await harness.endpoints.get_artwork_slots(_ROM_ID, _ROM_ID)

    assert _rows(slots)["hero"] == ("set_in_steam", "steam", None)


async def test_a_reported_cover_reads_chosen_and_without_its_record_set_in_steam(harness):
    grid = _grid(harness)
    (grid / f"{_ROM_ID}p.png").write_bytes(b"chosen cover")
    covers = harness.tmp_path / "cache" / "covers"
    covers.mkdir(parents=True)
    (covers / f"{_ROM_ID}.png").write_bytes(b"romm's cover, another size")
    with harness.uow_factory() as uow:
        uow.artwork_slot_choices.save(
            ArtworkSlotChoice(rom_id=_ROM_ID, slot=ArtworkSlot.COVER, image_id=5, chosen_at=1.0)
        )
    before = await harness.endpoints.get_artwork_slots(_ROM_ID, _ROM_ID)

    report = await harness.endpoints.report_sgdb_slot_artwork(
        _ROM_ID, "cover", "set", 9999, 5, hashlib.sha256(b"chosen cover").hexdigest()
    )
    after = await harness.endpoints.get_artwork_slots(_ROM_ID, _ROM_ID)

    assert report == {"success": True}
    assert _rows(before)["cover"] == ("set_in_steam", "steam", None)
    assert _rows(after)["cover"] == ("chosen", "chosen", 5)


async def test_a_cover_romm_has_none_for_answers_the_failure_shape_and_keeps_the_choice(harness):
    _grid(harness)
    with harness.uow_factory() as uow:
        uow.artwork_slot_choices.save(
            ArtworkSlotChoice(rom_id=_ROM_ID, slot=ArtworkSlot.COVER, image_id=5, chosen_at=1.0)
        )

    answer = await harness.endpoints.set_artwork_slot_automatic(_ROM_ID, "cover")

    assert answer == {"success": False, "reason": "no_cover", "message": "ROM has no cover artwork"}
    assert _choice(harness, ArtworkSlot.COVER).image_id == 5


async def test_an_image_not_listed_answers_the_failure_shape(harness):
    _grid(harness)
    _seed_logos(harness, 1)

    answer = await harness.endpoints.choose_artwork_slot_image(_ROM_ID, "logo", 99)

    assert answer == {
        "success": False,
        "reason": "image_not_found",
        "message": "SteamGridDB no longer lists this image for the game",
    }


async def test_without_a_game_the_picker_answers_the_failure_shape(harness):
    harness.settings["steamgriddb_api_key"] = "fake-sgdb-key"
    seed_rom(harness, _ROM_ID)

    answer = await harness.endpoints.get_artwork_slot_candidates(_ROM_ID, "logo", 0)

    assert answer == {
        "success": False,
        "reason": "no_sgdb_game",
        "message": "Choose a SteamGridDB game for this ROM first",
    }
