"""What the panel reads of a sync before a start it waits for, and what its stop answers (#2184).

Driven frontend-shaped through the real ``Endpoints``: the state endpoint names
the run the start waits for, the stop endpoint ends the wait, and the stopped
call answers with a reason of its own and nothing synced.
"""

from __future__ import annotations

import asyncio

from services.saves.sync_engine._gate import GateHolder

from ._seed import enable_save_sync, seed_install, seed_server_save


async def test_no_sync_before_a_start_is_idle_and_a_stop_finds_none(harness):
    assert harness.endpoints.pre_launch_sync_state(42) == {"state": "idle"}
    assert harness.endpoints.stop_pre_launch_sync(42) == {"success": True, "stopped": False}


async def test_a_start_waiting_behind_sync_all_is_told_so_and_its_stop_answers_nothing_synced(harness):
    enable_save_sync(harness)
    seed_install(harness, 42, system="gba", file_name="game.gba")
    seed_server_save(harness, save_id=100, rom_id=42)
    gate = harness.app.services.save_sync_service._sync_engine._device_gate

    async with gate.held_by(GateHolder.SYNC_ALL, max_wait=1):
        start = asyncio.ensure_future(harness.endpoints.pre_launch_sync(42))
        await asyncio.sleep(0.02)

        assert harness.endpoints.pre_launch_sync_state(42) == {"state": "waiting_for_sync", "holder": "sync_all"}
        assert harness.endpoints.stop_pre_launch_sync(42) == {"success": True, "stopped": True}
        result = await asyncio.wait_for(start, 1)

    assert result == {
        "success": False,
        "reason": "pre_launch_sync_stopped",
        "message": "Save sync stopped before the game started",
        "synced": 0,
        "uploaded": 0,
        "downloaded": 0,
    }
    assert not any(call[0] == "download_save_content" for call in harness.romm.call_log)
