"""Contract tests for the per-slot artwork fetch and its report (#2295).

Drives ``get_sgdb_slot_artwork`` and ``report_sgdb_slot_artwork`` over the real
wired backend, positionally and typed as ``frontend/src/api/backend.ts``
declares them: a slot with a file in Steam's grid directory is skipped, an empty
one is served with what the report passes back, the report is recorded in the
real ``artwork_slot_outcomes`` table, and what SteamGridDB has nothing for is not
asked again. The icon is judged by the icon path in a real ``shortcuts.vdf`` and
written by ``save_shortcut_icon``.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from _vendor import vdf

from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_outcome import SlotOutcome
from domain.shortcut_icon import PLACEHOLDER_ICON_PNG

from ._seed import seed_rom

_ROM_ID = 41
# Any PNG the real downscale reads; it answers one this small unchanged.
_PNG = PLACEHOLDER_ICON_PNG


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


async def test_a_cover_report_is_recorded_in_the_real_table(harness, grid):
    answer = await harness.endpoints.report_sgdb_slot_artwork(_ROM_ID, "cover", "set", 9999, 12, "ab" * 32)

    recorded = _outcome(harness, ArtworkSlot.COVER)
    assert answer == {"success": True}
    assert (recorded.outcome, recorded.image_id, recorded.image_sha256) == (SlotOutcome.SET, 12, "ab" * 32)


async def test_the_heros_report_is_recorded_in_the_real_table(harness, grid):
    report = await harness.endpoints.report_sgdb_slot_artwork(_ROM_ID, "hero", "set", 9999, 7, "ef" * 32)

    assert report == {"success": True}
    recorded = _outcome(harness, ArtworkSlot.HERO)
    assert (recorded.outcome, recorded.sgdb_id, recorded.image_id, recorded.image_sha256) == (
        SlotOutcome.SET,
        9999,
        7,
        "ef" * 32,
    )


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


async def test_the_placeholder_icon_is_served_written_and_its_report_recorded(harness, grid):
    url = "https://cdn.example/31.png"
    harness.sgdb.seed_raw_response(
        "/icons/game/9999", {"success": True, "data": [{"id": 31, "url": url, "width": 512, "height": 512}]}
    )
    harness.sgdb.seed_image_bytes(url, _PNG)
    shortcuts = {"0": {"appid": _ROM_ID, "AppName": "Game", "icon": str(grid / "tender-icon-placeholder.png")}}
    (grid.parent / "shortcuts.vdf").write_bytes(vdf.binary_dumps({"shortcuts": shortcuts}))

    answer = await harness.endpoints.get_sgdb_slot_artwork(_ROM_ID, _ROM_ID, "icon")
    await harness.endpoints.release_prune_conflict_lease(answer.pop("prune_lease_token"))
    saved = await harness.endpoints.save_shortcut_icon(_ROM_ID, _ROM_ID)
    report = await harness.endpoints.report_sgdb_slot_artwork(
        _ROM_ID, "icon", "set", saved["sgdb_id"], saved["image_id"], saved["sha256"]
    )

    assert answer == {"status": "ready"}
    written = (grid / f"{_ROM_ID}_icon.png").read_bytes()
    assert saved == {
        "success": True,
        "icon_path": str(grid / f"{_ROM_ID}_icon.png"),
        "image_id": 31,
        "sha256": hashlib.sha256(written).hexdigest(),
        "sgdb_id": 9999,
    }
    assert report == {"success": True}
    recorded = _outcome(harness, ArtworkSlot.ICON)
    assert (recorded.outcome, recorded.image_id, recorded.image_sha256) == (
        SlotOutcome.SET,
        31,
        hashlib.sha256(written).hexdigest(),
    )


async def test_an_icon_somebody_set_is_kept(harness, grid):
    shortcuts = {"0": {"appid": _ROM_ID, "AppName": "Game", "icon": "/home/deck/Pictures/mine.png"}}
    (grid.parent / "shortcuts.vdf").write_bytes(vdf.binary_dumps({"shortcuts": shortcuts}))

    answer = await harness.endpoints.get_sgdb_slot_artwork(_ROM_ID, _ROM_ID, "icon")

    assert answer == {"status": "skip"}
    assert harness.sgdb.requested_paths == []


async def test_a_replacing_fetch_serves_an_icon_somebody_set(harness, grid):
    url = "https://cdn.example/31.png"
    harness.sgdb.seed_raw_response(
        "/icons/game/9999", {"success": True, "data": [{"id": 31, "url": url, "width": 512, "height": 512}]}
    )
    harness.sgdb.seed_image_bytes(url, _PNG)
    shortcuts = {"0": {"appid": _ROM_ID, "AppName": "Game", "icon": "/home/deck/Pictures/mine.png"}}
    (grid.parent / "shortcuts.vdf").write_bytes(vdf.binary_dumps({"shortcuts": shortcuts}))

    answer = await harness.endpoints.get_sgdb_slot_artwork(_ROM_ID, _ROM_ID, "icon", True)
    await harness.endpoints.release_prune_conflict_lease(answer.pop("prune_lease_token"))

    assert answer == {"status": "ready"}


async def test_the_icon_asset_type_answers_the_failure_shape(harness, grid):
    answer = await harness.endpoints.get_sgdb_artwork_base64(_ROM_ID, 4)

    assert answer == {
        "success": False,
        "reason": "invalid_payload",
        "message": "The icon is served by the per-slot fetch",
    }
