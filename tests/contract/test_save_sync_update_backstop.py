"""What a manual save sync answers when an update starts while it waits for the device gate.

``sync_rom_saves`` asks the update rule at its entry; the sync engine asks
again once it holds the device gate, because an install pressed in between
would stop this process under the sync. Driven through the real ``Endpoints``,
so the answer is the one the panel reads.
"""

from __future__ import annotations

import contextlib

from ._harness import hold_update_in_progress
from ._seed import enable_save_sync, seed_install


async def test_an_update_started_while_the_sync_waited_is_refused_with_nothing_synced(harness, monkeypatch):
    enable_save_sync(harness)
    seed_install(harness, 42, system="gba", file_name="game.gba")
    gate = harness.app.services.save_sync_service._sync_engine._device_gate
    bounded_run = gate.bounded_run

    @contextlib.asynccontextmanager
    async def update_pressed_while_waiting(*, max_wait: float):
        async with bounded_run(max_wait=max_wait):
            hold_update_in_progress(harness)
            yield

    monkeypatch.setattr(gate, "bounded_run", update_pressed_while_waiting)

    result = await harness.endpoints.sync_rom_saves(42)

    assert result == {
        "success": False,
        "reason": "blocked_by_update",
        "message": "Tender is installing an update and will restart in a moment.",
        "synced": 0,
    }
    assert not any(call[0] == "upload_save" for call in harness.romm.call_log)
