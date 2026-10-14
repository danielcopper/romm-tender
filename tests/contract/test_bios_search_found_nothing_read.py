"""Contract tests for a BIOS answer with no file to show — DuckStation over an empty BIOS folder.

DuckStation names no BIOS file: it searches its folder and boots what it
recognises there. Over a folder holding nothing of a size it accepts, the real
resolver answers its verdict not met and the console's own image shown absent,
with no requirement at all — so the answer carries no row, and both reads have
to send it anyway. Everything below is the real resolver over a RetroDECK laid
down under the harness home, with DuckStation's own settings as RetroDECK writes
them: the search directory set, every per-region key empty.
"""

from __future__ import annotations

import os

from _standalone_firmware import SYSTEMS, lay_down_standalone_emulators

from ._seed import seed_es_systems, seed_rom


def _seed_duckstation(harness, *images: str) -> None:
    """The PlayStation with DuckStation as its one emulator, its BIOS folder holding *images*."""
    seed_es_systems(harness, SYSTEMS)
    home = os.path.join(str(harness.tmp_path), "home")
    lay_down_standalone_emulators(home, os.path.join(home, "retrodeck", "bios"), *images)


async def test_the_platform_reads_not_met_with_no_file_to_show(harness):
    _seed_duckstation(harness)

    answer = await harness.endpoints.get_platform_firmware_status("psx")

    entry = answer["platform"]
    assert entry is not None
    assert entry["files"] == []
    assert entry["active_core_label"] == "DuckStation (Standalone)"
    assert entry["bios_level"] == "missing"
    assert entry["console_needs_image"] is True
    assert [(note["kind"], os.path.basename(note["dir"]), note["candidates"]) for note in entry["emulator_notes"]] == [
        ("search-found-nothing", "bios", None)
    ]


async def test_the_play_button_warns(harness):
    _seed_duckstation(harness)
    seed_rom(harness, 7, platform_slug="psx")

    result = await harness.endpoints.get_bios_status(7)

    assert result["bios_status"] is not None
    assert result["bios_status"]["needs_bios"] is True
    assert result["bios_level"] == "missing"
    assert result["bios_label"] == "Missing"
    assert result["bios_status_unknown"] is False


async def test_a_file_its_search_keeps_is_no_longer_an_absence(harness):
    """The control: an image of the accepted size its table does not know still boots, so nothing is shown absent."""
    _seed_duckstation(harness, "unknown.bin")
    seed_rom(harness, 7, platform_slug="psx")

    result = await harness.endpoints.get_bios_status(7)

    assert result["bios_level"] != "missing"
    assert [row["file_name"] for row in result["bios_status"]["files"]] == ["unknown.bin"]
