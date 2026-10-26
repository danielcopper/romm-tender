"""Contract tests for the cover record of a version a sync binds (#2381 D28).

Drives real per-unit syncs over the real wired backend, acking with a
rom_id→appId map as ``report_unit_results`` would, then reads the ARTWORK tab:
a version that becomes a shortcut's bound one — a shortcut created anew after
removal, or a sync rebinding the shortcut onto another version — forgets the
record of a SteamGridDB cover Tender set for it earlier, so RomM's cover the
commit publishes never reads "Set in Steam".
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_outcome import ArtworkSlotOutcome
from domain.rom import Rom
from domain.sync_run_kind import SyncRunKind

if TYPE_CHECKING:
    import pathlib

_COVER = "/assets/romm/resources/roms/2/cover/big.png?ts=2026-07-11 12:00:00"


def _grid(harness) -> pathlib.Path:
    user = harness.tmp_path / "home" / ".steam" / "steam" / "userdata" / "12345"
    (user / "config" / "grid").mkdir(parents=True)
    return user / "config" / "grid"


def _stand_in_record(harness, rom_id: int) -> None:
    with harness.uow_factory() as uow:
        uow.artwork_slot_outcomes.save(
            ArtworkSlotOutcome.record_set(
                rom_id=rom_id,
                slot=ArtworkSlot.COVER,
                sgdb_id=9999,
                image_id=5,
                image_sha256=hashlib.sha256(b"an earlier steamgriddb cover").hexdigest(),
                recorded_at=1.0,
            )
        )


def _row(harness, rom_id: int, *, app_id: int | None, fs_name: str, name: str = "Game (USA)", **extra) -> None:
    with harness.uow_factory() as uow:
        uow.roms.save(
            Rom(
                rom_id=rom_id,
                platform_slug="saturn",
                name=name,
                fs_name=fs_name,
                shortcut_app_id=app_id,
                last_synced_at="2026-01-01T00:00:00",
                **extra,
            )
        )


async def _sync_acking(harness, bindings) -> None:
    async def ack(_unit, event):
        event.set()
        return {str(rom_id): app_id for rom_id, app_id in bindings(harness).items()}

    service = harness.app.services.sync_service
    service._orchestrator._unit_applier._chunk_dispatcher._wait_for_unit_complete = ack
    assert service._box.try_begin_run("run-1", kind=SyncRunKind.APPLY) is True
    await service._orchestrator._do_sync_per_unit()


async def _cover_row(harness, rom_id: int, app_id: int):
    slots = await harness.endpoints.get_artwork_slots(rom_id, app_id)
    cover = next(row for row in slots["slots"] if row["slot"] == "cover")
    return cover["state"], cover["source"]


async def test_a_shortcut_created_anew_does_not_read_its_old_steamgriddb_cover_as_set_in_steam(harness):
    grid = _grid(harness)
    harness.romm.platforms = [{"id": 1, "name": "Saturn", "slug": "saturn", "rom_count": 1}]
    harness.romm.roms[2] = {
        "id": 2,
        "name": "Game (USA)",
        "fs_name": "game.chd",
        "platform_id": 1,
        "platform_name": "Saturn",
        "platform_slug": "saturn",
        "path_cover_large": _COVER,
    }
    harness.romm.download_payloads[f"cover:{_COVER}"] = b"romm's cover"
    harness.settings["enabled_platforms"] = {"1": True}
    # The shortcut was removed: the row stays unbound, with the record of the SteamGridDB cover it showed.
    _row(harness, 2, app_id=None, fs_name="game.chd")
    _stand_in_record(harness, 2)

    await _sync_acking(harness, lambda _h: {2: 7777})

    assert (grid / "7777p.png").read_bytes() == b"romm's cover"
    assert await _cover_row(harness, 2, 7777) == ("automatic", "romm")


async def test_a_sync_rebinding_the_shortcut_does_not_read_the_versions_old_cover_as_set_in_steam(harness):
    grid = _grid(harness)
    harness.romm.platforms = [{"id": 1, "name": "Saturn", "slug": "saturn", "rom_count": 2}]
    harness.romm.roms[1] = {
        "id": 1,
        "name": "Game (USA)",
        "igdb_id": 100,
        "fs_name": "Game (USA).zip",
        "fs_name_no_ext": "Game (USA)",
        "platform_id": 1,
        "platform_name": "Saturn",
        "platform_slug": "saturn",
        "has_file_on_disk": False,
        "missing_from_fs": True,
    }
    harness.romm.roms[2] = {
        "id": 2,
        "name": "Game (USA)",
        "igdb_id": 100,
        "fs_name": "Game (USA)",
        "fs_name_no_ext": "Game (USA)",
        "platform_id": 1,
        "platform_name": "Saturn",
        "platform_slug": "saturn",
        "has_file_on_disk": True,
        "path_cover_large": _COVER,
    }
    harness.romm.download_payloads[f"cover:{_COVER}"] = b"romm's cover"
    harness.settings["enabled_platforms"] = {"1": True}
    _row(harness, 1, app_id=5000, fs_name="Game (USA).zip", sibling_group_key="igdb:100:1")
    # Version 2 was bound once and showed Tender's SteamGridDB cover then.
    _row(harness, 2, app_id=None, fs_name="Game (USA)", sibling_group_key="igdb:100:1")
    _stand_in_record(harness, 2)
    (grid / "5000p.png").write_bytes(b"version 1's cover")

    await _sync_acking(harness, lambda h: dict.fromkeys(h.app.services.sync_service._box.pending_sync, 5000))

    with harness.uow_factory() as uow:
        assert uow.roms.get(2).shortcut_app_id == 5000
    assert await _cover_row(harness, 2, 5000) == ("automatic", "romm")
