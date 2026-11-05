"""Tender refuses a RomM older than the floor it ships with (#2223 D29).

Over the real ``Endpoints`` and the floor bootstrap hands the connection,
``MIN_ROMM_VERSION``: a download reads the category RomM files each file under,
which only RomM 5.4.0 and later state by the folder directly below the game's
folder.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("version", ["5.4.0", "5.3.1", "5.4.1-beta.1"])
async def test_a_romm_older_than_5_4_1_is_refused(harness, version):
    harness.romm.heartbeat_response = {"SYSTEM": {"VERSION": version}}

    result = await harness.endpoints.connect_with_token("http://romm.local", "rmm_pasted", False)

    assert result["success"] is False
    assert result["reason"] == "version_error"
    assert "5.4.1" in result["message"]
    assert not harness.settings.get("romm_api_token")


async def test_romm_5_4_1_is_accepted(harness):
    harness.romm.heartbeat_response = {"SYSTEM": {"VERSION": "5.4.1"}}

    result = await harness.endpoints.connect_with_token("http://romm.local", "rmm_pasted", False)

    assert result["success"] is True
