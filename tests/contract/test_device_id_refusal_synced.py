"""What a save sync answers when the device-id entry refuses it, over the real wiring.

Each of the four sync paths hands ``SyncEngine.live_device_id`` its own refusal
details, the keys ``.claude/rules/endpoints.md`` gives a save-sync refusal, and
the entry attaches them to the refusal it raises. A slot switch hands it none,
and its refusal carries neither key.

With a device id stored, the entry only touches it and keeps it on every failure
but a 404, so no id is stored here: the entry registers the device, and the
fake fails that registration in three ways — as a server that cannot be reached
(``server_unreachable``), as one that refuses it, and as one that answers it
without an id (both ``device_not_registered``). The endpoints are called as the
panel calls them; the sync after a game is reached through
``finalize_game_session``, whose ``sync`` part carries that sync's answer.
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


# The toast the session's end shows for each failure: the refusal's own message.
_TOAST_FOR = {
    "unreachable": "Server unreachable — check your URL and ensure RomM is running",
    "refused": "Device not registered with RomM",
    "answered_without_an_id": "Device not registered with RomM",
}


@pytest.mark.parametrize(
    ("registration_fails", "toast"),
    [pytest.param(param.values[0], _TOAST_FOR[param.id], id=param.id) for param in _REGISTRATION_FAILURES],
)
async def test_the_sync_after_a_game_answers_the_refusal_with_nothing_synced(harness, registration_fails, toast):
    _game(harness)
    registration_fails(harness)

    result = await harness.endpoints.finalize_game_session(_ROM)

    assert result["sync"]["success"] is False
    assert result["sync"]["offline"] is False
    assert result["sync"]["failure_toast"] == toast
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
