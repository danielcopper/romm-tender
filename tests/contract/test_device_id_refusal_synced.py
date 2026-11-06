"""What a save sync answers when the device-id entry refuses it, over the real wiring.

Every save-sync refusal carries ``synced=0``, and the sweep's also ``conflicts=0``
(``.claude/rules/endpoints.md``); the device-id entry, ``SyncEngine.live_device_id``,
refuses where the device's registration fails, and each of the four sync paths
hands it its own refusal details. No device id is stored, so the entry registers
the device, and the fake fails that registration in each way the entry tells
apart: a server that cannot be reached, one that refuses the registration, and
one that answers it without an id. The endpoints are called as the panel calls
them; the sync after a game is reached through ``finalize_game_session``, whose
``sync`` part is what the panel reads of it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from domain.rom_save_sync_state import RomSaveSyncState
from lib.errors import RommConnectionError, RommForbiddenError

from ._seed import enable_save_sync, seed_install, seed_save_state

_ROM = 42

Arrange = Callable[[Any], None]


def _raises(error: Exception) -> Arrange:
    def arrange(harness) -> None:
        harness.romm.register_device_side_effect = error

    return arrange


def _answers_without_an_id(harness) -> None:
    harness.romm.register_device = lambda *args, **kwargs: {}


_REGISTRATION_FAILURES = [
    pytest.param(_raises(RommConnectionError("connection refused")), "server_unreachable", id="unreachable"),
    pytest.param(_raises(RommForbiddenError("HTTP 403: Forbidden")), "device_not_registered", id="refused"),
    pytest.param(_answers_without_an_id, "device_not_registered", id="answered_without_an_id"),
]


def _game(harness) -> None:
    enable_save_sync(harness)
    with harness.uow_factory() as uow:
        uow.kv_config.delete("device_id")
    seed_install(harness, _ROM, file_name="game.gba")
    seed_save_state(harness, _ROM, RomSaveSyncState(active_slot="default", slot_confirmed=True, system="gba"))


def _nothing_listed(harness) -> bool:
    return not any(call[0] == "list_saves" for call in harness.romm.call_log)


@pytest.mark.parametrize(("registration_fails", "reason"), _REGISTRATION_FAILURES)
async def test_sync_saves_answers_the_refusal_with_nothing_synced(harness, registration_fails, reason):
    _game(harness)
    registration_fails(harness)

    result = await harness.endpoints.sync_rom_saves(_ROM)

    assert result["success"] is False
    assert result["reason"] == reason
    assert result["synced"] == 0
    assert _nothing_listed(harness)


@pytest.mark.parametrize(("registration_fails", "reason"), _REGISTRATION_FAILURES)
async def test_sync_all_saves_answers_the_refusal_with_nothing_synced_and_no_conflicts(
    harness, registration_fails, reason
):
    _game(harness)
    registration_fails(harness)

    result = await harness.endpoints.sync_all_saves()

    assert result["success"] is False
    assert result["reason"] == reason
    assert result["synced"] == 0
    assert result["conflicts"] == 0
    assert _nothing_listed(harness)


@pytest.mark.parametrize(("registration_fails", "reason"), _REGISTRATION_FAILURES)
async def test_the_sync_before_a_start_answers_the_refusal_with_nothing_synced(harness, registration_fails, reason):
    _game(harness)
    registration_fails(harness)

    result = await harness.endpoints.pre_launch_sync(_ROM)

    assert result["success"] is False
    assert result["reason"] == reason
    assert result["synced"] == 0
    assert _nothing_listed(harness)


@pytest.mark.parametrize(
    "registration_fails", [pytest.param(param.values[0], id=param.id) for param in _REGISTRATION_FAILURES]
)
async def test_the_sync_after_a_game_answers_the_refusal_with_nothing_synced(harness, registration_fails):
    _game(harness)
    registration_fails(harness)

    result = await harness.endpoints.finalize_game_session(_ROM)

    assert result["sync"]["success"] is False
    assert result["sync"]["synced"] == 0
    assert _nothing_listed(harness)


@pytest.mark.parametrize(("registration_fails", "reason"), _REGISTRATION_FAILURES)
async def test_a_slot_switch_answers_the_refusal_without_the_sync_keys(harness, registration_fails, reason):
    _game(harness)
    registration_fails(harness)

    result = await harness.endpoints.switch_slot(_ROM, "other")

    assert result["success"] is False
    assert result["reason"] == reason
    assert "synced" not in result
    assert "conflicts" not in result
