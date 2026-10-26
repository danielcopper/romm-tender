"""Contract test: a library sync stopped by the backend's shutdown in the middle of an apply chunk.

Drives the real Endpoints through the real bootstrap. The run is started the way
the panel starts it, its first platform is acknowledged and committed, and its
second platform's chunk is left waiting for the panel when the sync service shuts
down. The next start's repair and stats read must then offer to resume.
"""

from __future__ import annotations

import asyncio


def _make_grid_resolvable(harness) -> None:
    """Materialise a Steam userdata dir so ``grid_dir()`` resolves for cover finalise."""
    (harness.tmp_path / "home" / ".steam" / "steam" / "userdata" / "12345").mkdir(parents=True)


def _seed_two_platforms(harness) -> None:
    harness.romm.platforms = [
        {"id": 1, "name": "N64", "slug": "n64", "rom_count": 1},
        {"id": 2, "name": "SNES", "slug": "snes", "rom_count": 1},
    ]
    harness.romm.roms[10] = {
        "id": 10,
        "name": "Game",
        "fs_name": "game.z64",
        "platform_id": 1,
        "platform_name": "N64",
        "platform_slug": "n64",
    }
    harness.romm.roms[20] = {
        "id": 20,
        "name": "Other",
        "fs_name": "other.sfc",
        "platform_id": 2,
        "platform_name": "SNES",
        "platform_slug": "snes",
    }
    harness.settings["enabled_platforms"] = {"1": True, "2": True}


async def test_a_run_cancelled_at_shutdown_in_the_middle_of_a_chunk_leaves_the_resume_offer(harness):
    _make_grid_resolvable(harness)
    _seed_two_platforms(harness)
    second_chunk_waiting = asyncio.Event()

    async def the_panel_acks_n64_only(unit, event):
        if unit.slug == "n64":
            event.set()
            return {"10": 7777}
        second_chunk_waiting.set()
        await asyncio.Event().wait()
        return None

    sync_service = harness.app.services.sync_service
    sync_service._orchestrator._unit_applier._chunk_dispatcher._wait_for_unit_complete = the_panel_acks_n64_only

    assert (await harness.endpoints.start_sync())["success"] is True
    await asyncio.wait_for(second_chunk_waiting.wait(), 10)
    await asyncio.wait_for(sync_service.shutdown(), 10)

    harness.app.services.startup_healing_service.reconcile_orphaned_sync_runs()
    stats = harness.endpoints.get_sync_stats()

    assert stats["last_attempt"]["status"] == "interrupted"
    assert stats["roms"] == 1
    assert stats["resumable_games"] == 1
    assert stats["has_completion_stamp"] is True
