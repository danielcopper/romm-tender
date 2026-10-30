"""Contract test: the library sync's gone-from-RomM mark, and what the removed-game cleanup makes of it (#2431).

A version the library reached only through a collection, and a version on a
platform RomM no longer lists, carry no evidence a platform stamp can give. The
apply records RomM's answer on the row instead; the cleanup's list adds every
marked row to the candidates its stamps give, and the mark goes when a sync
delivers the row again, when RomM answers its exact id with the ROM, or with the
row itself.

Drives the real sync orchestrator, the real commit, the real cleanup and the
real QAM count read over real SQLite under ``tmp_path``; RomM's transport is the
fake, and the frontend's ack is the one stand-in.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from domain.platform_sync_state import PlatformSyncState
from domain.rom import GoneEvidence, Rom
from domain.sync_run_kind import SyncRunKind
from domain.version_metadata import VersionMetadata
from lib.errors import RommConnectionError, RommNotFoundError

pytestmark = pytest.mark.usefixtures("seeded_retrodeck")

_GBA_ID = 2
_N64_ID = 1
_FAVES_ID = 7
_STAYS = 20
_GONE = 21


def _server_rom(rom_id: int, *, platform_id: int, slug: str, name: str) -> dict[str, Any]:
    return {
        "id": rom_id,
        "name": name,
        "fs_name": f"{name}.rom",
        "fs_name_no_ext": name,
        "platform_id": platform_id,
        "platform_name": slug.upper(),
        "platform_slug": slug,
        "has_file_on_disk": True,
    }


def _ack_every_shortcut(harness) -> None:
    sync = harness.app.services.sync_service

    async def ack(_unit, event):
        event.set()
        return {str(rom_id): 6000 + rom_id for rom_id in sync._box.pending_sync}

    sync._orchestrator._unit_applier._chunk_dispatcher._wait_for_unit_complete = ack


async def _apply(harness, run_id: str) -> None:
    """Apply a run, then report what its ``sync_stale`` named as the panel does once it saw the shortcuts gone."""
    sync = harness.app.services.sync_service
    assert sync._box.try_begin_run(run_id, kind=SyncRunKind.APPLY) is True
    start = len(harness.emit.call_args_list)
    await sync._orchestrator._do_sync_per_unit()
    removed = [
        row
        for call in harness.emit.call_args_list[start:]
        if call.args and call.args[0] == "sync_stale"
        for row in call.args[1]["remove"]
    ]
    if removed:
        stale = [{"rom_id": row["rom_id"], "app_id": row["app_id"]} for row in removed if "kept_app_id" not in row]
        converged = [{"rom_id": row["rom_id"], "app_id": row["app_id"]} for row in removed if "kept_app_id" in row]
        answer = await harness.endpoints.report_sync_removals(stale, converged)
        assert answer["success"] is True


def _mark(harness, rom_id: int) -> str | None:
    with harness.uow_factory() as uow:
        row = uow.roms.get(rom_id)
    assert row is not None
    return row.gone_from_romm


async def _listed(harness) -> set[int]:
    preview = await harness.endpoints.get_prune_preview(
        {"scope": "bulk", "rom_id": None, "preview_id": None, "offset": 0, "limit": 50}
    )
    assert preview["success"] is True
    return {item["rom_id"] for item in preview["items"] if item["candidate"]}


async def _bind_both_through_faves_then_romm_deletes_21(harness) -> None:
    """GBA's sync is off; Faves reaches games 20 and 21 there. Then RomM deletes 21."""
    harness.romm.platforms = [{"id": _GBA_ID, "name": "GBA", "slug": "gba", "rom_count": 2}]
    for rom_id, name in ((_STAYS, "A"), (_GONE, "B")):
        harness.romm.roms[rom_id] = {
            **_server_rom(rom_id, platform_id=_GBA_ID, slug="gba", name=name),
            "collection_ids": [_FAVES_ID],
        }
    harness.romm.collections.append(
        {
            "id": _FAVES_ID,
            "name": "Faves",
            "rom_count": 2,
            "rom_ids": [_STAYS, _GONE],
            "is_favorite": False,
            "is_virtual": False,
            "updated_at": "2025-01-01T00:00:00",
        }
    )
    harness.settings["enabled_platforms"] = {str(_GBA_ID): False}
    harness.settings["enabled_collections"] = {"standard": {str(_FAVES_ID): True}}
    _ack_every_shortcut(harness)
    await _apply(harness, "run-1")
    with harness.uow_factory() as uow:
        assert uow.roms.get(_GONE).shortcut_app_id == 6000 + _GONE

    faves = harness.romm.collections[0]
    faves["rom_ids"] = [_STAYS]
    faves["rom_count"] = 1
    faves["updated_at"] = "2030-01-01T00:00:00"
    harness.romm.platforms[0]["rom_count"] = 1
    harness.romm.roms.pop(_GONE)
    harness.romm.get_rom_once_side_effect_by_id[_GONE] = RommNotFoundError("Rom with id 21 not found")


def _romm_serves_21_in_faves_again(harness) -> None:
    harness.romm.get_rom_once_side_effect_by_id.pop(_GONE)
    harness.romm.roms[_GONE] = {
        **_server_rom(_GONE, platform_id=_GBA_ID, slug="gba", name="B"),
        "collection_ids": [_FAVES_ID],
    }
    faves = harness.romm.collections[0]
    faves["rom_ids"] = [_STAYS, _GONE]
    faves["rom_count"] = 2
    faves["updated_at"] = "2031-01-01T00:00:00"
    harness.romm.platforms[0]["rom_count"] = 2


async def test_a_version_reached_through_a_collection_and_404_is_listed_until_a_sync_delivers_it(harness):
    await _bind_both_through_faves_then_romm_deletes_21(harness)
    assert await _listed(harness) == set()

    await _apply(harness, "run-2")

    assert _mark(harness, _GONE) == "not_found"
    with harness.uow_factory() as uow:
        assert uow.roms.get(_GONE).shortcut_app_id == 6000 + _GONE
    assert await _listed(harness) == {_GONE}

    _romm_serves_21_in_faves_again(harness)
    await _apply(harness, "run-3")

    assert _mark(harness, _GONE) is None
    assert await _listed(harness) == set()


async def test_a_preview_asks_the_same_question_and_marks_nothing(harness):
    await _bind_both_through_faves_then_romm_deletes_21(harness)
    harness.romm.call_log.clear()

    preview = await harness.app.services.sync_service.sync_preview()

    assert preview["success"] is True
    assert ("get_rom_once", (_GONE,), {}) in harness.romm.call_log
    assert _mark(harness, _GONE) is None
    assert await _listed(harness) == set()


async def test_the_sync_s_own_answer_with_the_rom_clears_the_mark(harness):
    """21 comes back on RomM outside Faves: the apply removes its shortcut and its mark with it."""
    await _bind_both_through_faves_then_romm_deletes_21(harness)
    await _apply(harness, "run-2")
    assert _mark(harness, _GONE) == "not_found"

    harness.romm.get_rom_once_side_effect_by_id.pop(_GONE)
    await _apply(harness, "run-3")

    with harness.uow_factory() as uow:
        assert uow.roms.get(_GONE).shortcut_app_id is None
    assert _mark(harness, _GONE) is None
    assert await _listed(harness) == set()


@pytest.mark.parametrize("marked_before", [False, True])
async def test_an_answer_that_establishes_nothing_writes_nothing(harness, marked_before):
    await _bind_both_through_faves_then_romm_deletes_21(harness)
    if marked_before:
        await _apply(harness, "run-2")
    harness.romm.get_rom_once_side_effect_by_id[_GONE] = RommConnectionError("connection refused")

    await _apply(harness, "run-3")

    assert _mark(harness, _GONE) == ("not_found" if marked_before else None)


async def _bind_n64_then_romm_stops_listing_it(harness) -> None:
    """N64 and GBA are on; then RomM's platform list no longer names N64."""
    harness.romm.platforms = [
        {"id": _N64_ID, "name": "N64", "slug": "n64", "rom_count": 1},
        {"id": _GBA_ID, "name": "GBA", "slug": "gba", "rom_count": 1},
    ]
    harness.romm.roms[1] = _server_rom(1, platform_id=_N64_ID, slug="n64", name="N")
    harness.romm.roms[4] = _server_rom(4, platform_id=_GBA_ID, slug="gba", name="G")
    harness.settings["enabled_platforms"] = {str(_N64_ID): True, str(_GBA_ID): True}
    _ack_every_shortcut(harness)
    await _apply(harness, "run-1")
    harness.romm.platforms = [p for p in harness.romm.platforms if p["slug"] != "n64"]


async def test_a_version_kept_on_a_platform_romm_no_longer_lists_is_marked_and_listed(harness):
    await _bind_n64_then_romm_stops_listing_it(harness)
    assert await _listed(harness) == set()

    await _apply(harness, "run-2")

    assert _mark(harness, 1) == "delisted"
    with harness.uow_factory() as uow:
        assert uow.roms.get(1).shortcut_app_id == 6001
    assert await _listed(harness) == {1}


async def test_the_platform_count_leaves_out_exactly_what_the_cleanup_lists(harness):
    await _bind_n64_then_romm_stops_listing_it(harness)
    await _apply(harness, "run-2")

    platforms = {p["slug"]: p for p in harness.endpoints.get_registry_platforms()["platforms"]}

    assert await _listed(harness) == {1}
    assert (platforms["n64"]["count"], platforms["n64"]["reachable_count"]) == (1, 0)
    assert (platforms["gba"]["count"], platforms["gba"]["reachable_count"]) == (1, 1)


def _seed_marked_row(harness, rom_id: int, *, slug: str, evidence: GoneEvidence, fetch_id: str | None = None) -> None:
    rom = Rom.synced(
        rom_id=rom_id,
        platform_slug=slug,
        name=f"Game {rom_id}",
        fs_name=f"Game {rom_id}.rom",
        shortcut_app_id=None,
        synced_at="2026-01-01T00:00:00",
    )
    if fetch_id is not None:
        rom.record_fetch_generation(fetch_id)
    rom.mark_gone_from_romm(evidence)
    with harness.uow_factory() as uow:
        uow.roms.save(rom)


def _seed_control(harness, rom_id: int = 900041) -> None:
    """A row RomM is known to have served, on a platform of its own, so a 404 round has a canary."""
    rom = Rom.synced(
        rom_id=rom_id,
        platform_slug="snes",
        name="Kept Game",
        fs_name="Kept Game.sfc",
        shortcut_app_id=None,
        synced_at="2026-01-01T00:00:00",
    )
    rom.record_fetch_generation("snes-fetch")
    with harness.uow_factory() as uow:
        uow.roms.save(rom)
        uow.platform_sync_state.save(
            PlatformSyncState.stamp(platform_slug="snes", at="2026-01-02T00:00:00", rom_count=1, fetch_id="snes-fetch")
        )


async def _run_the_cleanup(harness) -> dict[str, Any]:
    preview = await harness.endpoints.get_prune_preview(
        {"scope": "bulk", "rom_id": None, "preview_id": None, "offset": 0, "limit": 50}
    )
    started = await harness.endpoints.start_prune(
        {
            "preview_id": preview["preview_id"],
            "confirmed": True,
            "remove_rows": True,
            "remove_fully_vanished": True,
            "create_recovery_bundle": False,
            "include_installed_rom_ids": [],
        }
    )
    assert started["success"] is True
    task = harness.app.services.prune_service._task
    assert task is not None
    await task
    complete = [item.args[1] for item in harness.emit.await_args_list if item.args[0] == "prune_complete"]
    assert len(complete) == 1
    return complete[0]


async def test_the_cleanup_finding_the_rom_on_romm_clears_its_mark(harness):
    _seed_marked_row(harness, 21, slug="gba", evidence="not_found")
    assert await _listed(harness) == {21}

    payload = await _run_the_cleanup(harness)

    assert payload["removed_rom_ids"] == []
    assert _mark(harness, 21) is None
    assert await _listed(harness) == set()


async def test_a_row_the_cleanup_removes_takes_its_mark_with_it(harness):
    _seed_marked_row(harness, 21, slug="gba", evidence="not_found")
    _seed_control(harness)
    harness.romm.get_rom_once_side_effect_by_id[21] = RommNotFoundError("Rom with id 21 not found")

    payload = await _run_the_cleanup(harness)

    assert payload["removed_rom_ids"] == [21]
    with harness.uow_factory() as uow:
        assert uow.roms.get(21) is None
    assert await _listed(harness) == set()


async def test_a_gone_game_on_a_delisted_platform_is_removed_though_the_lowest_served_ids_are_marked(harness):
    """Three N64 games RomM no longer lists keep their stamp's generation; none of them may vouch for a 404."""
    with harness.uow_factory() as uow:
        uow.platform_sync_state.save(
            PlatformSyncState.stamp(platform_slug="n64", at="2026-01-02T00:00:00", rom_count=3, fetch_id="n64-fetch")
        )
    for rom_id in (1, 2, 3):
        _seed_marked_row(harness, rom_id, slug="n64", evidence="delisted", fetch_id="n64-fetch")
        harness.romm.get_rom_once_side_effect_by_id[rom_id] = RommNotFoundError(f"Rom with id {rom_id} not found")
    _seed_control(harness)

    payload = await _run_the_cleanup(harness)

    assert payload["removed_rom_ids"] == [1, 2, 3]


async def test_a_mark_that_goes_between_the_list_and_the_start_refuses_the_start(harness):
    _seed_marked_row(harness, 21, slug="gba", evidence="not_found")
    preview = await harness.endpoints.get_prune_preview(
        {"scope": "bulk", "rom_id": None, "preview_id": None, "offset": 0, "limit": 50}
    )
    with harness.uow_factory() as uow:
        row = uow.roms.get(21)
        row.clear_gone_from_romm()
        uow.roms.save(row)

    started = await harness.endpoints.start_prune(
        {
            "preview_id": preview["preview_id"],
            "confirmed": True,
            "remove_rows": True,
            "remove_fully_vanished": True,
            "create_recovery_bundle": False,
            "include_installed_rom_ids": [],
        }
    )

    assert (started["success"], started["reason"]) == (False, "stale_preview")


def _seed_group_member(
    harness, rom_id: int, *, app_id: int | None = None, evidence: GoneEvidence | None = None
) -> None:
    rom = Rom.synced(
        rom_id=rom_id,
        platform_slug="gba",
        name="Madagascar",
        fs_name=f"Madagascar ({rom_id}).gba",
        shortcut_app_id=app_id,
        synced_at="2026-01-01T00:00:00",
        version=VersionMetadata(sibling_group_key="igdb:1:2"),
    )
    if evidence is not None:
        rom.mark_gone_from_romm(evidence)
    with harness.uow_factory() as uow:
        uow.roms.save(rom)


async def test_a_version_the_run_proves_gone_outside_its_candidates_is_marked_and_listed_next(harness):
    """D5: 31 is no candidate (its platform has no stamp), yet the run's group probe proves it gone."""
    _seed_group_member(harness, 30, evidence="not_found")
    _seed_group_member(harness, 31)
    _seed_group_member(harness, 32, app_id=7032)
    _seed_control(harness)
    for rom_id in (30, 31):
        harness.romm.get_rom_once_side_effect_by_id[rom_id] = RommNotFoundError(f"Rom with id {rom_id} not found")
    assert await _listed(harness) == {30}

    payload = await _run_the_cleanup(harness)

    assert payload["removed_rom_ids"] == [30]
    assert _mark(harness, 31) == "not_found"
    assert _mark(harness, 32) is None
    assert await _listed(harness) == {31}


async def test_a_404_the_run_could_not_confirm_marks_nothing(harness):
    """No version answered and the only control 404s too, so the round has no proof."""
    _seed_group_member(harness, 30, evidence="not_found")
    _seed_group_member(harness, 31)
    _seed_control(harness)
    for rom_id in (30, 31, 900041):
        harness.romm.get_rom_once_side_effect_by_id[rom_id] = RommNotFoundError("misrouted")

    payload = await _run_the_cleanup(harness)

    assert payload["removed_rom_ids"] == []
    assert _mark(harness, 31) is None
    assert await _listed(harness) == {30}


async def test_a_kept_candidate_the_run_proves_gone_carries_the_mark(harness):
    """40 is a stamp candidate the run keeps (its shortcut is on it, 41 is on RomM); the run still marks it gone."""
    _seed_group_member(harness, 40, app_id=7040)
    _seed_group_member(harness, 41)
    with harness.uow_factory() as uow:
        for rom_id, fetch_id in ((40, "older-fetch"), (41, "gba-fetch")):
            row = uow.roms.get(rom_id)
            assert row is not None
            row.record_fetch_generation(fetch_id)
            uow.roms.save(row)
        uow.platform_sync_state.save(
            PlatformSyncState.stamp(platform_slug="gba", at="2026-01-02T00:00:00", rom_count=1, fetch_id="gba-fetch")
        )
    harness.romm.get_rom_once_side_effect_by_id[40] = RommNotFoundError("Rom with id 40 not found")
    assert await _listed(harness) == {40}

    payload = await _run_the_cleanup(harness)

    assert payload["results"][0]["reason"] == "shortcut_version_kept"
    assert _mark(harness, 40) == "not_found"
    assert await _listed(harness) == {40}


def _answer_in_turn(harness, answers: dict[int, list[object]]) -> None:
    """Each listed id gets its answers in order, the last one repeating; every other id is served."""

    def get_rom_once(rom_id: int) -> dict[str, Any]:
        harness.romm.call_log.append(("get_rom_once", (rom_id,), {}))
        queue = answers.get(rom_id)
        answer = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else {"id": rom_id}
        if isinstance(answer, Exception):
            raise answer
        return dict(cast("dict[str, Any]", answer))

    harness.romm.get_rom_once = get_rom_once


async def test_a_marked_candidate_that_answers_both_ways_in_one_run_keeps_its_mark(harness):
    """30 404s in its group's check and answers at the re-check before removal: the run settles nothing."""
    _seed_group_member(harness, 30, evidence="not_found")
    _seed_group_member(harness, 32, app_id=7032)
    _answer_in_turn(harness, {30: [RommNotFoundError("Rom with id 30 not found"), {"id": 30}]})

    payload = await _run_the_cleanup(harness)

    assert payload["removed_rom_ids"] == []
    assert _mark(harness, 30) == "not_found"


async def test_a_version_that_answers_both_ways_in_one_run_gains_no_mark(harness):
    """31 is no candidate: it 404s in its group's check, then answers as the control of 30's re-check."""
    _seed_group_member(harness, 30, evidence="not_found")
    _seed_group_member(harness, 31)
    _seed_group_member(harness, 32, app_id=7032)
    with harness.uow_factory() as uow:
        for rom_id in (31, 32):
            row = uow.roms.get(rom_id)
            assert row is not None
            row.record_fetch_generation("gba-fetch")
            uow.roms.save(row)
        uow.platform_sync_state.save(
            PlatformSyncState.stamp(platform_slug="gba", at="2026-01-02T00:00:00", rom_count=2, fetch_id="gba-fetch")
        )
    assert await _listed(harness) == {30}
    _answer_in_turn(
        harness,
        {
            30: [RommNotFoundError("Rom with id 30 not found")],
            31: [RommNotFoundError("Rom with id 31 not found"), {"id": 31}],
        },
    )

    payload = await _run_the_cleanup(harness)

    assert payload["removed_rom_ids"] == [30]
    assert _mark(harness, 31) is None
