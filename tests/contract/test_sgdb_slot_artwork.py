"""Contract tests for the per-slot artwork fetch and its report (#2295).

Drives ``get_sgdb_slot_artwork`` and ``report_sgdb_slot_artwork`` over the real
wired backend, positionally and typed as ``frontend/src/api/backend.ts``
declares them: a slot with a file in Steam's grid directory is skipped, an empty
one is served with what the report passes back, the report is recorded in the
real ``artwork_slot_outcomes`` table, and what SteamGridDB has nothing for is not
asked again.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_outcome import SlotOutcome

from ._seed import seed_rom

_ROM_ID = 41


@pytest.fixture
def grid(harness):
    """Rom 41 bound to its own app id, with SteamGridDB id 9999, a key, and an empty grid directory."""
    harness.settings["steamgriddb_api_key"] = "fake-sgdb-key"
    seed_rom(harness, _ROM_ID)
    with harness.uow_factory() as uow:
        rom = uow.roms.get(_ROM_ID)
        rom.assign_sgdb_id(9999)
        uow.roms.save(rom)
    path = harness.tmp_path / "home" / ".local" / "share" / "Steam" / "userdata" / "123" / "config" / "grid"
    path.mkdir(parents=True)
    return path


def _outcome(harness, slot: ArtworkSlot):
    with harness.uow_factory() as uow:
        return uow.artwork_slot_outcomes.get(_ROM_ID, slot)


async def test_an_empty_wide_grid_is_served_and_its_report_recorded(harness, grid):
    url = "https://cdn.example/12.png"
    harness.sgdb.seed_raw_response(
        "/grids/game/9999", {"success": True, "data": [{"id": 12, "url": url, "width": 920, "height": 430}]}
    )
    harness.sgdb.seed_image_bytes(url, b"wide grid")

    answer = await harness.endpoints.get_sgdb_slot_artwork(_ROM_ID, _ROM_ID, "grid")
    await harness.endpoints.release_prune_conflict_lease(answer.pop("prune_lease_token"))
    report = await harness.endpoints.report_sgdb_slot_artwork(
        _ROM_ID, "grid", "set", answer["sgdb_id"], answer["image_id"], answer["sha256"]
    )

    assert answer == {
        "status": "image",
        "base64": base64.b64encode(b"wide grid").decode("ascii"),
        "image_id": 12,
        "sha256": hashlib.sha256(b"wide grid").hexdigest(),
        "sgdb_id": 9999,
    }
    assert report == {"success": True}
    recorded = _outcome(harness, ArtworkSlot.WIDE_GRID)
    assert (recorded.outcome, recorded.image_id, recorded.image_sha256) == (
        SlotOutcome.SET,
        12,
        hashlib.sha256(b"wide grid").hexdigest(),
    )


async def test_a_slot_with_a_file_is_skipped(harness, grid):
    (grid / f"{_ROM_ID}_logo.jpg").write_bytes(b"set by hand")

    answer = await harness.endpoints.get_sgdb_slot_artwork(_ROM_ID, _ROM_ID, "logo")

    assert answer == {"status": "skip"}
    assert harness.sgdb.requested_paths == []


async def test_a_logo_steamgriddb_has_none_for_is_not_asked_again(harness, grid):
    harness.sgdb.seed_raw_response("/logos/game/9999", {"success": True, "data": []})

    first = await harness.endpoints.get_sgdb_slot_artwork(_ROM_ID, _ROM_ID, "logo")
    second = await harness.endpoints.get_sgdb_slot_artwork(_ROM_ID, _ROM_ID, "logo")

    assert (first, second) == ({"status": "none"}, {"status": "skip"})
    assert harness.sgdb.requested_paths == ["/logos/game/9999?types=static"]
    assert _outcome(harness, ArtworkSlot.LOGO).outcome is SlotOutcome.NONE


async def test_refresh_artwork_deletes_the_games_outcomes(harness, grid):
    harness.sgdb.seed_raw_response("/logos/game/9999", {"success": True, "data": []})
    await harness.endpoints.get_sgdb_slot_artwork(_ROM_ID, _ROM_ID, "logo")

    await harness.endpoints.get_sgdb_resolution(_ROM_ID)

    assert _outcome(harness, ArtworkSlot.LOGO) is None


async def test_an_unknown_slot_answers_the_failure_shape(harness, grid):
    answer = await harness.endpoints.get_sgdb_slot_artwork(_ROM_ID, _ROM_ID, "banner")

    assert answer == {"success": False, "reason": "invalid_payload", "message": "Unknown artwork slot: banner"}


async def test_a_hero_report_answers_the_failure_shape(harness, grid):
    answer = await harness.endpoints.report_sgdb_slot_artwork(_ROM_ID, "hero", "set", 9999, None, "ab")

    assert answer == {
        "success": False,
        "reason": "invalid_payload",
        "message": "No outcome is recorded for the hero slot",
    }


async def test_the_jobs_icon_report_is_recorded_in_the_real_table(harness, grid):
    report = await harness.endpoints.report_sgdb_slot_artwork(_ROM_ID, "icon", "set", 9999, 31, "cd" * 32)

    assert report == {"success": True}
    recorded = _outcome(harness, ArtworkSlot.ICON)
    assert (recorded.outcome, recorded.sgdb_id, recorded.image_id, recorded.image_sha256) == (
        SlotOutcome.SET,
        9999,
        31,
        "cd" * 32,
    )


async def test_the_icon_is_not_served_by_the_per_slot_fetch(harness, grid):
    answer = await harness.endpoints.get_sgdb_slot_artwork(_ROM_ID, _ROM_ID, "icon")

    assert answer == {
        "success": False,
        "reason": "invalid_payload",
        "message": "The icon slot is not served by the per-slot fetch",
    }
