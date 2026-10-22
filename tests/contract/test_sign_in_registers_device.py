"""Every successful sign-in registers this device, whether save sync is on or off.

Driven frontend-shaped over the real ``Endpoints`` and ``bootstrap``, as the
sign-in contract tests are: each of the three sign-ins
(``connect_with_credentials``, ``connect_with_token``,
``connect_with_pairing_code``) is called with save sync off, and the device's
registration is read off the fake RomM's call log and the stored id the save
service answers. A registration that fails leaves the sign-in a success.
"""

from __future__ import annotations

from typing import Any

from lib.errors import RommConnectionError

_URL = "http://romm.local"


def _sign_in_ready(harness) -> None:
    harness.settings["save_sync_enabled"] = False
    harness.romm.heartbeat_response = {"SYSTEM": {"VERSION": "5.3.0"}}
    harness.romm.exchange_pairing_code_response = {"id": 3, "raw_token": "rmm_paired"}


async def _sign_in(harness, how: str) -> dict[str, Any]:
    if how == "credentials":
        return await harness.endpoints.connect_with_credentials(_URL, "user", "pass", False)
    if how == "token":
        return await harness.endpoints.connect_with_token(_URL, "rmm_pasted", False)
    return await harness.endpoints.connect_with_pairing_code(_URL, "ABCD2345", False)


def _registrations(harness) -> list[Any]:
    return [call for call in harness.romm.call_log if call[0] == "register_device"]


async def test_a_credentials_sign_in_registers_the_device_with_save_sync_off(harness):
    _sign_in_ready(harness)

    result = await _sign_in(harness, "credentials")

    assert result["success"] is True
    assert len(_registrations(harness)) == 1
    assert harness.app.services.save_sync_service.get_device_id() == "device-1"


async def test_a_token_sign_in_registers_the_device_with_save_sync_off(harness):
    _sign_in_ready(harness)

    result = await _sign_in(harness, "token")

    assert result["success"] is True
    assert len(_registrations(harness)) == 1
    assert harness.app.services.save_sync_service.get_device_id() == "device-1"


async def test_a_pairing_code_sign_in_registers_the_device_with_save_sync_off(harness):
    _sign_in_ready(harness)

    result = await _sign_in(harness, "pairing")

    assert result["success"] is True
    assert len(_registrations(harness)) == 1
    assert harness.app.services.save_sync_service.get_device_id() == "device-1"


async def test_a_registration_that_fails_leaves_the_sign_in_a_success(harness):
    _sign_in_ready(harness)
    harness.romm.register_device_side_effect = RommConnectionError("connection refused")

    result = await _sign_in(harness, "token")

    assert result["success"] is True
    assert harness.settings["romm_api_token"] == "rmm_pasted"
    assert harness.app.services.save_sync_service.get_device_id() is None


async def test_a_failed_sign_in_registers_nothing(harness):
    _sign_in_ready(harness)
    harness.romm.get_current_user_side_effect = RommConnectionError("connection refused")
    harness.romm.heartbeat_response = {"SYSTEM": {"VERSION": "5.3.0"}}

    result = await _sign_in(harness, "token")

    assert result["success"] is False
    assert _registrations(harness) == []
