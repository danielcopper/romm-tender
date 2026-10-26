"""Contract test: every platform the sync files is named by RomM's platform listing (#2564).

N64's slug, ``n64``, and its listed name, ``Nintendo 64``, differ, and a custom
name set on it in RomM, ``N64 Custom``, is neither (D2). N64's sync is off;
Faves reaches its game 20, and platform groups are on, so the apply files 20
under N64. No platform unit names N64, so the name can come only from the run's
own platform listing (D1).

Drives the real sync orchestrator, the real commit, the real platform removal
and the real game-detail read over real SQLite under ``tmp_path``; RomM's
transport is the fake, and the frontend's ack is the one stand-in.
"""

from __future__ import annotations

from typing import Any

import pytest

from domain.rom import Rom
from domain.sync_run import SyncRun
from domain.sync_run_kind import SyncRunKind

pytestmark = pytest.mark.usefixtures("seeded_retrodeck")

_N64_ID = 4
_FAVES_ID = 7
_GAME = 20
_APP_ID = 6000 + _GAME


def _romm_lists_n64_and_faves_reaches_20(harness) -> None:
    harness.romm.platforms = [
        {
            "id": _N64_ID,
            "name": "Nintendo 64",
            "custom_name": "N64 Custom",
            "display_name": "N64 Custom",
            "slug": "n64",
            "rom_count": 1,
        }
    ]
    harness.romm.roms[_GAME] = {
        "id": _GAME,
        "name": "Golf",
        "fs_name": "Golf.z64",
        "fs_name_no_ext": "Golf",
        "platform_id": _N64_ID,
        "platform_name": "N64 Custom",
        "platform_display_name": "N64 Custom",
        "platform_slug": "n64",
        "has_file_on_disk": True,
        "collection_ids": [_FAVES_ID],
    }
    harness.romm.collections.append(
        {
            "id": _FAVES_ID,
            "name": "Faves",
            "rom_count": 1,
            "rom_ids": [_GAME],
            "is_favorite": False,
            "is_virtual": False,
            "updated_at": "2025-01-01T00:00:00",
        }
    )
    harness.settings["enabled_platforms"] = {str(_N64_ID): False}
    harness.settings["enabled_collections"] = {"standard": {str(_FAVES_ID): True}}
    harness.settings["collection_create_platform_groups"] = True

    sync = harness.app.services.sync_service

    async def ack(_unit, event):
        event.set()
        return {str(rom_id): 6000 + rom_id for rom_id in sync._box.pending_sync}

    sync._orchestrator._unit_applier._chunk_dispatcher._wait_for_unit_complete = ack


async def _apply(harness, run_id: str) -> dict[str, Any]:
    """The ``platform_app_ids`` the apply sends."""
    sync = harness.app.services.sync_service
    starts_at = len(harness.emit.call_args_list)
    assert sync._box.try_begin_run(run_id, kind=SyncRunKind.APPLY) is True
    await sync._orchestrator._do_sync_per_unit()
    (platform_app_ids,) = [
        c.args[1]["platform_app_ids"]
        for c in harness.emit.call_args_list[starts_at:]
        if c.args and c.args[0] == "sync_collections"
    ]
    return platform_app_ids


def _an_older_version_filed_20_under_the_slug(harness) -> None:
    """20 is bound, the name cache holds nothing for N64, and the last run sent its collection as ``n64``."""
    with harness.uow_factory() as uow:
        uow.roms.save(
            Rom.synced(
                rom_id=_GAME,
                platform_slug="n64",
                name="Golf",
                fs_name="Golf.z64",
                shortcut_app_id=_APP_ID,
                synced_at="2025-01-01T00:00:00Z",
            )
        )
        run = SyncRun.start(id="run-0", at="2025-01-01T00:00:00Z", platforms_planned=0, roms_planned=1)
        run.complete("2025-01-01T00:01:00Z", platforms=["n64"], collections=["Faves"])
        uow.sync_runs.save(run)


async def test_a_game_only_a_collection_reaches_is_filed_and_shown_under_its_platform_s_listed_name(harness):
    _romm_lists_n64_and_faves_reaches_20(harness)

    platform_app_ids = await _apply(harness, "run-1")

    assert platform_app_ids == {"Nintendo 64": [_APP_ID]}
    detail = await harness.endpoints.get_cached_game_detail(_APP_ID)
    assert detail["found"] is True
    assert detail["platform_name"] == "Nintendo 64"


async def test_a_collection_named_by_the_slug_is_replaced_and_the_platform_s_removal_finds_the_new_one(harness):
    _romm_lists_n64_and_faves_reaches_20(harness)
    _an_older_version_filed_20_under_the_slug(harness)

    preview = await harness.app.services.sync_service.sync_preview()
    harness.app.services.sync_service._box.discard_preview()
    platform_app_ids = await _apply(harness, "run-1")

    assert preview["summary"]["platform_collection_diff"] == {
        "has_changes": True,
        "added_count": 1,
        "removed_count": 1,
    }
    assert platform_app_ids == {"Nintendo 64": [_APP_ID]}
    removal = await harness.endpoints.remove_platform_shortcuts("n64")
    assert removal["success"] is True
    assert removal["app_ids"] == [_APP_ID]
    assert removal["platform_name"] == "Nintendo 64"
