"""Contract test for the RomM URL save over the real ``Endpoints``.

Driven frontend-shaped per ``frontend/src/api/backend.ts``:
``saveServerUrl = endpoint<[string, boolean], ServerUrlResult>``. What this tier
adds over the service unit tests is the wire answer the RomM URL window reads —
the completed URL beside ``reason`` and ``message`` — and the settings the next
read reports.
"""

from __future__ import annotations

_HTTPS_ADDED = "Added https:// — press OK to save it, or change it to http:// if your RomM answers only on http."


async def test_a_url_without_a_scheme_is_answered_with_the_https_url_and_not_saved(harness):
    before = harness.endpoints.get_settings()["romm_url"]

    result = await harness.endpoints.save_server_url("  romm.example.com:8080  ", False)

    assert result == {
        "success": False,
        "reason": "scheme_missing",
        "message": _HTTPS_ADDED,
        "completed_url": "https://romm.example.com:8080",
    }
    assert harness.endpoints.get_settings()["romm_url"] == before


async def test_a_second_ok_saves_the_completed_url(harness):
    first = await harness.endpoints.save_server_url("192.168.1.5:8080", False)

    second = await harness.endpoints.save_server_url(first["completed_url"], False)

    assert second == {"success": True, "message": "Settings saved"}
    assert harness.endpoints.get_settings()["romm_url"] == "https://192.168.1.5:8080"


async def test_a_url_with_a_scheme_tender_does_not_accept_is_refused_as_before(harness):
    before = harness.endpoints.get_settings()["romm_url"]

    result = await harness.endpoints.save_server_url("ftp://romm.example.com", False)

    assert result == {"success": False, "reason": "config_error", "message": "Enter a valid http(s):// server URL"}
    assert harness.endpoints.get_settings()["romm_url"] == before
