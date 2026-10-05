"""What a manual save sync answers when the sync engine's own update guard refuses it.

``sync_rom_saves`` asks the update rule at its entry, and its operation on the
prune conflicts makes a press wait from then on; the engine asks again once it
holds the device gate, for a caller that reached it some other way. The test
sets the update flag once the gate is held, so the engine's refusal is the one
answered, and drives the real ``Endpoints``, so the answer is the one the panel
reads.
"""

from __future__ import annotations

import contextlib

from ._harness import hold_update_in_progress
from ._seed import enable_save_sync, seed_install


async def test_the_engine_s_update_refusal_reaches_the_panel_with_nothing_synced(harness, monkeypatch):
    enable_save_sync(harness)
    seed_install(harness, 42, system="gba", file_name="game.gba")
    gate = harness.app.services.save_sync_service._sync_engine._device_gate
    bounded_run = gate.bounded_run

    @contextlib.asynccontextmanager
    async def update_flag_set_once_the_gate_is_held(*, max_wait: float):
        async with bounded_run(max_wait=max_wait):
            hold_update_in_progress(harness)
            yield

    monkeypatch.setattr(gate, "bounded_run", update_flag_set_once_the_gate_is_held)

    result = await harness.endpoints.sync_rom_saves(42)

    assert result == {
        "success": False,
        "reason": "blocked_by_update",
        "message": "Tender is installing an update and will restart in a moment.",
        "synced": 0,
    }
    assert not any(call[0] == "upload_save" for call in harness.romm.call_log)
