"""Contract test for the custom proxy-header callable (#1822).

Driven frontend-shaped per ``frontend/src/api/backend.ts``:
``saveCustomHeaders = callable<[CustomHeaderEntry[]], BackendResult>`` and the
``romm_custom_header_names`` field ``getSettings`` reports.

What this tier adds over the service unit tests is the round trip through the
real ``Plugin`` and the real settings persistence: the list survives a write and
comes back as NAMES with no value attached, which is the half a unit test with a
mocked persister cannot show.
"""

from __future__ import annotations

import json


def _set(name: str, value: str) -> dict[str, str]:
    return {"name": name, "value_action": "set", "value": value}


def _keep(name: str) -> dict[str, str]:
    return {"name": name, "value_action": "keep"}


async def test_save_custom_headers_persists_and_reads_back_names_only(harness):
    assert (await harness.plugin.get_settings())["romm_custom_header_names"] == []

    entries = [_set("P-Access-Token", "s3cret"), _set("P-Access-Token-Id", "7")]
    assert await harness.plugin.save_custom_headers(entries) == {"success": True}

    settings = await harness.plugin.get_settings()
    assert settings["romm_custom_header_names"] == ["P-Access-Token", "P-Access-Token-Id"]
    assert "s3cret" not in json.dumps(settings)


async def test_keep_survives_a_round_trip_through_the_settings_file(harness):
    await harness.plugin.save_custom_headers([_set("P-Access-Token", "s3cret")])

    assert await harness.plugin.save_custom_headers([_keep("P-Access-Token")]) == {"success": True}

    assert harness.plugin.settings["romm_custom_headers"] == [{"name": "P-Access-Token", "value": "s3cret"}]


async def test_a_refused_list_returns_the_canonical_failure_shape(harness):
    result = await harness.plugin.save_custom_headers([_set("Authorization", "Basic abc")])

    assert set(result) == {"success", "reason", "message"}
    assert result["success"] is False
    assert result["reason"] == "authorization_reserved"
    assert result["message"]
    assert "error" not in result
    assert "error_code" not in result
    assert harness.plugin.settings["romm_custom_headers"] == []
