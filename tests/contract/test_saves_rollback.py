"""Contract tests for ``saves_rollback_to_version`` — restoring an older version of a save.

Driven frontend-shaped per ``frontend/src/api/backend.ts``: ``savesRollbackToVersion``
is ``endpoint<[number, string, number], …>``. The restore answers its own
discriminated status union; a RomM error at the download of the chosen version
is not one of them, and reaches the panel as the canonical
``{success: False, reason, message}`` failure the endpoint's translation builds.
"""

from __future__ import annotations

from domain.rom_save_sync_state import RomSaveSyncState
from lib.errors import RommReportedServerError

from ._seed import enable_save_sync, seed_install, seed_save_state, seed_server_save


async def test_a_romm_error_at_the_download_answers_the_failure_shape(harness, monkeypatch):
    """The chosen version's download draws a RomM error → ``classify_error``'s reason and message, and no PUT."""
    enable_save_sync(harness)
    seed_install(harness, 42, system="gba", file_name="game.gba")
    seed_server_save(
        harness, save_id=100, rom_id=42, slot="default", file_name="game.srm", updated_at="2026-03-01T00:00:00Z"
    )
    seed_server_save(
        harness, save_id=50, rom_id=42, slot="default", file_name="game.srm", updated_at="2026-02-01T00:00:00Z"
    )
    seed_save_state(harness, 42, RomSaveSyncState(active_slot="default", system="gba"))

    download = harness.romm.download_save_content

    def download_refusing_the_chosen_version(save_id, dest_path, **kwargs):
        if save_id == 50:
            raise RommReportedServerError("RomM answered 500", 500, detail="Internal Server Error")
        download(save_id, dest_path, **kwargs)

    monkeypatch.setattr(harness.romm, "download_save_content", download_refusing_the_chosen_version)

    result = await harness.endpoints.saves_rollback_to_version(42, "default", 50)

    assert result == {
        "success": False,
        "reason": "server_error",
        "message": "RomM reported an error (HTTP 500) — Internal Server Error",
    }
    assert not any(c[0] == "upload_save" and c[2]["save_id"] == 50 for c in harness.romm.call_log)
