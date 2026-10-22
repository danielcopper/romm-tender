"""Every save operation reaches RomM through ``SyncEngine.live_device_id``.

The entry registers or heals this device before an operation's first request to
RomM and answers the id the operation then sends. These tests drive each save
operation that asks RomM for this device's saves over the real ``SaveService``
and the in-memory ``FakeSaveApi``, and read the order and the arguments of the
requests off the fake's ``call_log``: with no id stored the device is registered
before anything is listed and the fresh id is the one sent; a stored id the
server has lost is replaced before it is sent; and a registration that fails
ends the operation with its decided answer before anything is listed or moved.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from lib.errors import Refused, RommConnectionError, RommForbiddenError, RommNotFoundError
from tests.services.saves._helpers import (
    _create_save,
    _file_md5,
    _install_rom,
    _require_save_state,
    _seed_save_state_dict,
    _server_save,
    _server_save_with_syncs,
    _uow,
    make_service,
)

# What FakeSaveApi mints for the first device it registers.
_FRESH_ID = "device-1"


def _store_device_id(svc, device_id: str | None) -> None:
    """Write the stored id behind the registry's back, and drop its cache."""
    with _uow(svc) as uow:
        if device_id is None:
            uow.kv_config.delete("device_id")
        else:
            uow.kv_config.set("device_id", device_id)
    svc._sync_engine._devices.invalidate_device_id_cache()


def _ready(tmp_path, *, save_sync: bool = True):
    """Rom 42 installed and confirmed on ``autosave``, its local save in sync with save 100.

    The server also holds a legacy save (10, a copy and migration source) and a
    save in a second slot (``other``, 101) the slot operations name. No device id
    is stored, and the version is known, so the entry's first request is the
    registration itself.
    """
    svc, fake = make_service(tmp_path)
    svc._config.settings["save_sync_enabled"] = save_sync
    fake.set_version("5.3.0")
    _install_rom(svc, tmp_path)
    save_path = _create_save(tmp_path, content=b"local-bytes")
    local_hash = _file_md5(str(save_path))
    _seed_save_state_dict(
        svc,
        42,
        {
            "system": "gba",
            "active_slot": "autosave",
            "slot_confirmed": True,
            "slots": {"autosave": {"source": "server"}, "other": {"source": "server"}},
            "files": {"pokemon.srm": {"tracked_save_id": 100, "last_sync_hash": local_hash}},
        },
    )
    fake.saves[100] = _server_save_with_syncs(save_id=100, slot="autosave", updated_at="2026-03-10T10:00:00Z")
    fake.saves[100]["content_hash"] = local_hash
    fake.set_server_save_content(100, b"local-bytes")
    fake.saves[10] = _server_save(save_id=10, slot=None, updated_at="2026-02-01T10:00:00Z")
    fake.set_server_save_content(10, b"legacy-bytes")
    fake.saves[101] = _server_save(save_id=101, slot="other", updated_at="2026-02-05T10:00:00Z")
    fake.set_server_save_content(101, b"other-bytes")
    return svc, fake


Operation = Callable[[Any], Awaitable[Any]]

# Every save operation that asks RomM for this device's saves, by name.
_OPERATIONS: dict[str, Operation] = {
    "copy_save_to_slot": lambda svc: svc.copy_save_to_slot(42, 10, "promoted"),
    "rollback_to_version": lambda svc: svc.rollback_to_version(42, "autosave", 100),
    "resolve_sync_conflict": lambda svc: svc.resolve_sync_conflict(42, "pokemon.srm", 100, "use_server"),
    "get_save_status": lambda svc: svc.get_save_status(42),
    "get_save_setup_info": lambda svc: svc.get_save_setup_info(42),
    "list_file_versions": lambda svc: svc.list_file_versions(42, "autosave", "pokemon.srm"),
    "confirm_slot_choice_migration": lambda svc: svc.confirm_slot_choice(42, "default", True, None),
    "switch_slot": lambda svc: svc.switch_slot(42, "other"),
    "get_slot_saves": lambda svc: svc.get_slot_saves(42, "other"),
    "get_slot_delete_info": lambda svc: svc.get_slot_delete_info(42, "other"),
    "delete_slot": lambda svc: svc.delete_slot(42, "other"),
    "sync_rom_saves": lambda svc: svc.sync_rom_saves(42),
}

# How each operation answers a failed registration: a reads' own failure field, or the refusal raised.
_ANSWERED_IN_A_FIELD: dict[str, Callable[[dict[str, Any]], str | None]] = {
    "get_save_status": lambda result: result["server_query_reason"] if result["server_query_failed"] else None,
    "get_save_setup_info": lambda result: result["recommended_action"] if result["server_query_failed"] else None,
    "list_file_versions": lambda result: result["status"],
}


async def _answer_to_a_failed_registration(name: str, svc) -> str | None:
    """The reason *name* answers with, read from its failure field or from the refusal it raises."""
    if name in _ANSWERED_IN_A_FIELD:
        return _ANSWERED_IN_A_FIELD[name](await _OPERATIONS[name](svc))
    with pytest.raises(Refused) as refusal:
        await _OPERATIONS[name](svc)
    return refusal.value.reason


def _calls(fake, name: str) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    return [call for call in fake.call_log if call[0] == name]


def _first(fake, name: str) -> int:
    indices = [i for i, call in enumerate(fake.call_log) if call[0] == name]
    assert indices, f"no {name} request was sent"
    return indices[0]


_TRANSFERS = ("upload_save", "download_save_content", "download_save", "delete_server_saves", "confirm_download")


class TestTheEntry:
    @pytest.mark.asyncio
    async def test_with_no_id_stored_it_registers_and_answers_the_fresh_id(self, tmp_path):
        svc, fake = _ready(tmp_path)

        assert await svc._sync_engine.live_device_id() == _FRESH_ID
        assert len(_calls(fake, "register_device")) == 1

    @pytest.mark.asyncio
    async def test_it_registers_with_save_sync_off(self, tmp_path):
        svc, fake = _ready(tmp_path, save_sync=False)

        assert await svc._sync_engine.live_device_id() == _FRESH_ID
        assert len(_calls(fake, "register_device")) == 1

    @pytest.mark.asyncio
    async def test_a_live_id_is_touched_and_answered(self, tmp_path):
        svc, fake = _ready(tmp_path)
        _store_device_id(svc, "device-7")

        assert await svc._sync_engine.live_device_id() == "device-7"
        assert [c[1] for c in _calls(fake, "update_device")] == [("device-7",)]
        assert _calls(fake, "register_device") == []

    @pytest.mark.asyncio
    async def test_a_dead_id_is_replaced_by_a_fresh_one(self, tmp_path):
        svc, fake = _ready(tmp_path)
        _store_device_id(svc, "dead-7")
        fake.fail_on_next(RommNotFoundError("HTTP 404: Not Found"))

        assert await svc._sync_engine.live_device_id() == _FRESH_ID
        assert svc.get_device_id() == _FRESH_ID

    @pytest.mark.asyncio
    async def test_two_concurrent_calls_register_once(self, tmp_path):
        svc, fake = _ready(tmp_path)

        answers = await asyncio.gather(svc._sync_engine.live_device_id(), svc._sync_engine.live_device_id())

        assert answers == [_FRESH_ID, _FRESH_ID]
        assert len(_calls(fake, "register_device")) == 1

    @pytest.mark.asyncio
    async def test_a_registration_that_cannot_reach_romm_is_server_unreachable(self, tmp_path):
        svc, fake = _ready(tmp_path)
        fake.fail_on_next(RommConnectionError("connection refused"))

        with pytest.raises(Refused) as refusal:
            await svc._sync_engine.live_device_id()

        assert refusal.value.reason == "server_unreachable"

    @pytest.mark.asyncio
    async def test_a_registration_romm_refuses_is_device_not_registered(self, tmp_path):
        svc, fake = _ready(tmp_path)
        fake.fail_on_next(RommForbiddenError("HTTP 403: Forbidden"))

        with pytest.raises(Refused) as refusal:
            await svc._sync_engine.live_device_id()

        assert refusal.value.reason == "device_not_registered"

    @pytest.mark.asyncio
    async def test_a_registration_answered_without_an_id_is_device_not_registered(self, tmp_path):
        svc, fake = _ready(tmp_path)
        fake.arm_register_device_without_id()

        with pytest.raises(Refused) as refusal:
            await svc._sync_engine.live_device_id()

        assert refusal.value.reason == "device_not_registered"
        assert svc.get_device_id() is None


class TestEveryOperationRegistersFirst:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", sorted(_OPERATIONS))
    async def test_with_no_id_stored_it_registers_before_listing_and_sends_the_fresh_id(self, tmp_path, name):
        svc, fake = _ready(tmp_path)

        # What the operation answers is not the question here; what it sent RomM is.
        with contextlib.suppress(Refused):
            await _OPERATIONS[name](svc)

        listings = _calls(fake, "list_saves")
        assert listings, "the operation never listed the saves"
        assert _first(fake, "register_device") < _first(fake, "list_saves")
        assert {call[2]["device_id"] for call in listings} == {_FRESH_ID}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", sorted(_OPERATIONS))
    async def test_a_dead_id_is_healed_before_it_is_sent(self, tmp_path, name):
        svc, fake = _ready(tmp_path)
        _store_device_id(svc, "dead-7")
        fake.fail_on_next(RommNotFoundError("HTTP 404: Not Found"))

        with contextlib.suppress(Refused):
            await _OPERATIONS[name](svc)

        listings = _calls(fake, "list_saves")
        assert listings, "the operation never listed the saves"
        assert {call[2]["device_id"] for call in listings} == {_FRESH_ID}


class TestAFailedRegistrationEndsTheOperation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", sorted(_OPERATIONS))
    async def test_romm_unreachable_answers_server_unreachable_and_lists_nothing(self, tmp_path, name):
        svc, fake = _ready(tmp_path)
        fake.fail_on_next(RommConnectionError("connection refused"))

        assert await _answer_to_a_failed_registration(name, svc) == "server_unreachable"
        assert _calls(fake, "list_saves") == []
        assert [call for call in fake.call_log if call[0] in _TRANSFERS] == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", sorted(_OPERATIONS))
    async def test_romm_answering_without_registering_answers_device_not_registered(self, tmp_path, name):
        svc, fake = _ready(tmp_path)
        fake.arm_register_device_without_id()

        assert await _answer_to_a_failed_registration(name, svc) == "device_not_registered"
        assert _calls(fake, "list_saves") == []
        assert [call for call in fake.call_log if call[0] in _TRANSFERS] == []

    @pytest.mark.asyncio
    async def test_a_copy_and_a_restore_leave_the_save_state_as_it_was(self, tmp_path):
        svc, fake = _ready(tmp_path)
        before = copy.deepcopy(_require_save_state(svc, 42))

        fake.arm_register_device_without_id()
        with pytest.raises(Refused):
            await svc.copy_save_to_slot(42, 10, "promoted")
        fake.arm_register_device_without_id()
        with pytest.raises(Refused):
            await svc.rollback_to_version(42, "autosave", 100)

        assert _require_save_state(svc, 42) == before

    @pytest.mark.asyncio
    async def test_a_slot_choice_migration_confirms_nothing(self, tmp_path):
        svc, fake = _ready(tmp_path)
        fake.arm_register_device_without_id()

        with pytest.raises(Refused):
            await svc.confirm_slot_choice(42, "default", True, None)

        assert _require_save_state(svc, 42).active_slot == "autosave"
