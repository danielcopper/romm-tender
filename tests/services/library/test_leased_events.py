"""The two library-sync events whose Steam writes outlive the run carry a lease the library takes itself.

``sync_complete`` always carries one; ``sync_stale`` only when it removes a
shortcut. A lease nobody can release — the emit raised, or nobody heard the
event — is given back at once, or it would hold off every cleanup until it
expires.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.services.library._helpers import _seed_rom_row


async def _emit_sync_complete(plugin) -> None:
    await plugin._sync_service._reporter.emit_sync_complete(
        platform_app_ids={},
        romm_collection_app_ids={},
        total_games=0,
        cancelled=False,
        interrupt_reason=None,
        restart_recommended=False,
    )


async def _finalize(plugin, *, synced_rom_ids: set[int]) -> None:
    await plugin._sync_service._orchestrator._finalize_per_unit(
        synced_rom_ids=synced_rom_ids,
        collection_memberships={},
        platform_rom_ids=synced_rom_ids,
        platform_names={},
        cancelled=False,
    )


def _payloads(emit, event: str) -> list[dict[str, Any]]:
    return [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == event]


class TestSyncComplete:
    async def test_it_carries_a_lease_that_holds_off_a_cleanup(self, plugin, emit):
        await _emit_sync_complete(plugin)

        (payload,) = _payloads(emit, "sync_complete")
        assert payload["prune_lease_token"].startswith("sync_complete:")
        assert plugin._prune_conflicts.conflicting_operations == 1
        assert await plugin._prune_conflicts.reserve_start("start_prune") is not None

    async def test_one_nobody_heard_gives_its_lease_back(self, plugin, emit):
        emit.return_value = False

        await _emit_sync_complete(plugin)

        (payload,) = _payloads(emit, "sync_complete")
        assert payload["prune_lease_token"].startswith("sync_complete:")
        assert plugin._prune_conflicts.conflicting_operations == 0

    async def test_one_whose_emit_raises_gives_its_lease_back(self, plugin, emit):
        emit.side_effect = RuntimeError("transport rejected event")

        with pytest.raises(RuntimeError, match="transport rejected event"):
            await _emit_sync_complete(plugin)

        assert plugin._prune_conflicts.conflicting_operations == 0


class TestSyncStale:
    async def test_one_that_removes_a_shortcut_carries_a_lease(self, plugin, emit):
        _seed_rom_row(plugin, 99, app_id=9900, platform_slug="gba")

        await _finalize(plugin, synced_rom_ids=set())

        (payload,) = _payloads(emit, "sync_stale")
        assert payload["remove"] == [{"rom_id": 99, "app_id": 9900}]
        assert payload["prune_lease_token"].startswith("sync_stale:")
        assert plugin._prune_conflicts.conflicting_operations == 1

    async def test_one_that_removes_nothing_takes_no_lease(self, plugin, emit):
        _seed_rom_row(plugin, 10, app_id=1000, platform_slug="n64")

        await _finalize(plugin, synced_rom_ids={10})

        assert _payloads(emit, "sync_stale") == [{"remove": []}]
        assert plugin._prune_conflicts.conflicting_operations == 0

    async def test_one_nobody_heard_gives_its_lease_back(self, plugin, emit):
        _seed_rom_row(plugin, 99, app_id=9900, platform_slug="gba")
        emit.return_value = False

        await _finalize(plugin, synced_rom_ids=set())

        (payload,) = _payloads(emit, "sync_stale")
        assert payload["prune_lease_token"].startswith("sync_stale:")
        assert plugin._prune_conflicts.conflicting_operations == 0
