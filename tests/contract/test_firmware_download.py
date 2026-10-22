"""Contract tests for the per-row BIOS download over the real nesting (#164).

``download_platform_firmware_file`` is the Library page's per-file Download
button. It addresses the file by its NAME in RomM — the platform's own listing
first, then the whole library, since a file fills a requirement whichever
platform RomM files it under — so what this tier pins is the file a press
fetches, plus the two answers a press can get that the service tier states in
isolation: a name the library does not hold, and a file already in the store.

The bytes land in Tender's firmware store through the real
``FirmwareFileAdapter`` under ``tmp_path``, and the store record lands through
the real Unit of Work, so a passing happy path also proves where the file lies
and the record a later BIOS delete is authorized by.
"""

from __future__ import annotations

import hashlib
import os

import pytest

from lib.errors import RommConnectionError

# Every download, adoption and removal here lands in RetroDECK's folders.
pytestmark = pytest.mark.usefixtures("seeded_retrodeck")

_DC_FIRMWARE = [
    {
        "id": 1,
        "file_name": "dc_boot.bin",
        "file_size_bytes": 8,
        "md5_hash": hashlib.md5(b"bootbios").hexdigest(),
    },
    {
        "id": 2,
        "file_name": "dc_flash.bin",
        "file_size_bytes": 8,
        "md5_hash": hashlib.md5(b"flashbio").hexdigest(),
    },
]


async def test_downloads_the_named_file_and_records_it(harness):
    harness.romm.seed_firmware("dc", *(dict(f) for f in _DC_FIRMWARE))
    harness.romm.download_payloads["firmware:1:dc_boot.bin"] = b"bootbios"

    result = await harness.endpoints.download_platform_firmware_file("dc", "dc_boot.bin")

    assert result["success"] is True
    assert result["downloaded"] == 1
    with harness.uow_factory() as uow:
        record = uow.firmware_store.get("dc", "dc_boot.bin")
    assert record is not None
    assert record.firmware_id == 1
    assert os.path.exists(os.path.join(record.folder, "bios", "dc", "dc_boot.bin"))
    # The sibling row is untouched — one press fetches one file.
    with harness.uow_factory() as uow:
        assert uow.firmware_store.get("dc", "dc_flash.bin") is None


async def test_a_second_press_finds_the_file_already_here(harness):
    harness.romm.seed_firmware("dc", *(dict(f) for f in _DC_FIRMWARE))
    harness.romm.download_payloads["firmware:1:dc_boot.bin"] = b"bootbios"

    assert (await harness.endpoints.download_platform_firmware_file("dc", "dc_boot.bin"))["downloaded"] == 1
    result = await harness.endpoints.download_platform_firmware_file("dc", "dc_boot.bin")

    assert result["success"] is True
    assert result["downloaded"] == 0
    assert "already here" in result["message"]


async def test_a_name_the_library_does_not_hold_is_refused(harness):
    harness.romm.seed_firmware("dc", *(dict(f) for f in _DC_FIRMWARE))

    result = await harness.endpoints.download_platform_firmware_file("n64", "nope.bin")

    assert result["success"] is False
    assert result["reason"] == "not_in_library"
    assert isinstance(result["message"], str)
    assert result["message"]
    assert "downloaded" not in result
    assert "download_firmware" not in [name for name, _args, _kwargs in harness.romm.call_log]


async def test_an_unreachable_server_reports_its_own_reason(harness):
    harness.romm.seed_firmware("dc", *(dict(f) for f in _DC_FIRMWARE))
    harness.romm.download_firmware_side_effect = RommConnectionError("Connection reset")

    result = await harness.endpoints.download_platform_firmware_file("dc", "dc_boot.bin")

    assert result["success"] is False
    assert result["reason"]
    assert "downloaded" not in result
    with harness.uow_factory() as uow:
        assert uow.firmware_store.get("dc", "dc_boot.bin") is None
