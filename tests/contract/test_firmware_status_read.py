"""Contract tests for the Library page's BIOS read — the two calls that make it.

The page asks `get_firmware_status` which platforms it can speak for and then
`get_platform_firmware_status` for one platform at a time, because a platform's
state costs a live per-system reading. What the reader ends up looking at is the
two joined, and the join is the thing nothing else pins: the service tests state
each half on its own, and the ~25 that describe the whole-page answer compose it
through a local helper, which would reproduce a composition bug rather than
catch one.

So this tier assembles the way the frontend will — the skeleton call, then one
per-platform call per platform it named — and holds the result against what the
single whole-page call answered before the split. The key sets below are read off
that implementation (`a5777746`), not off the code under test, which is what
makes them an oracle rather than a restatement.

**One half of the composition is out of this tier's reach**: no BIOS root exists
under the harness's `tmp_path`, so the real resolver answers unresolved for every
system and no reading here is ever complete. Which means the withheld answer —
`platform: None`, the drop the whole-page call performed before returning — can
only be reached where the reading can be stated rather than taken, and that is
`tests/services/test_firmware.py::TestOnePlatformsOwnEntry`. `_compose` below
passes such a platform over the way the page does, so the composition is written
for it even though nothing here produces one.
"""

from __future__ import annotations

from typing import Any

from ._seed import seed_es_systems, seed_rom

_FIRMWARE = [
    {"id": 1, "file_name": "dc_boot.bin", "file_path": "bios/dc/dc_boot.bin", "file_size_bytes": 8, "md5_hash": "a"},
    {"id": 2, "file_name": "dc_flash.bin", "file_path": "bios/dc/dc_flash.bin", "file_size_bytes": 4, "md5_hash": ""},
    {"id": 3, "file_name": "gba_bios.bin", "file_path": "bios/gba/gba_bios.bin", "file_size_bytes": 16, "md5_hash": ""},
]

# Every key the whole-page answer carried per platform, at a5777746: the entry's
# identity and rows, the emulator info, the two flags, the delete count and the
# aggregates. A field lost between the two calls fails here rather than on the
# pane that stops rendering it.
_ENTRY_KEYS = {
    "platform_slug",
    "files",
    "has_games",
    "all_downloaded",
    "active_core",
    "active_core_label",
    "emulators",
    "emulator_data_available",
    "deletable_count",
    "server_count",
    "local_count",
    "required_count",
    "required_downloaded",
    "required_withheld",
    "bios_level",
    "system_image",
}

# And every key a row carried: the server's own fields, the destination and its
# presence, the machine's answer about the file, and the row's delete count.
_ROW_KEYS = {
    "id",
    "file_name",
    "size",
    "md5",
    "on_server",
    "local_path",
    "downloaded",
    "declared_path",
    "description",
    "wanted",
    "required_by_active",
    "system_image_candidate",
    "supplied_by",
    "satisfied",
    "declared_kind",
    "caveats",
    "images",
    "deletable_count",
}


async def _compose(harness) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The page's whole answer, assembled the way the page assembles it.

    Returns the skeleton and the entries it led to. Asserts the join on the way
    through, because a platform that answers under a different slug than the one
    it was asked about — or answers nothing where the skeleton named it — is a
    composition failure rather than a result to carry on with.
    """
    skeleton = await harness.plugin.get_firmware_status()
    assert skeleton["success"] is True
    entries: list[dict[str, Any]] = []
    for named in skeleton["platforms"]:
        answer = await harness.plugin.get_platform_firmware_status(named["platform_slug"])
        assert answer["success"] is True
        entry = answer["platform"]
        if entry is None:
            continue
        assert entry["platform_slug"] == named["platform_slug"]
        # The one field both halves carry, and so the only join the payload can
        # be checked on: the two calls read it from one DB read each, and a
        # platform whose halves disagree about it has had two different
        # questions answered.
        assert entry["has_games"] == named["has_games"]
        entries.append(entry)
    return skeleton, entries


async def test_the_two_calls_compose_the_whole_page_answer(harness):
    """Skeleton plus per-platform answers rebuild the payload the page renders."""
    seed_es_systems(harness)
    seed_rom(harness, 1, platform_slug="gba")  # bound → has_games
    harness.romm.firmware_files = [dict(f) for f in _FIRMWARE]

    skeleton, entries = await _compose(harness)

    # Both platforms the library holds firmware for, in the order the overview
    # named them, each carrying the whole entry the pane reads.
    assert [p["platform_slug"] for p in skeleton["platforms"]] == ["dc", "gba"]
    assert [e["platform_slug"] for e in entries] == ["dc", "gba"]
    for entry in entries:
        assert set(entry) == _ENTRY_KEYS
        for row in entry["files"]:
            assert set(row) == _ROW_KEYS

    dc = entries[0]
    gba = entries[1]
    assert [row["file_name"] for row in dc["files"]] == ["dc_boot.bin", "dc_flash.bin"]
    assert [row["id"] for row in dc["files"]] == [1, 2]
    assert [row["size"] for row in dc["files"]] == [8, 4]
    assert all(row["on_server"] is True for row in dc["files"])
    assert dc["server_count"] == 2
    assert dc["has_games"] is False
    assert gba["has_games"] is True
    assert gba["active_core"] == "mgba_libretro.so"


async def test_the_listing_is_fetched_once_for_the_whole_walk(harness):
    """One round trip for the library, however many platforms are asked about.

    The reading is per platform; what the RomM library holds is not. The listing
    cache is what keeps the second half true, and a walk that re-fetched it per
    platform would put a network call between every row and the next.
    """
    seed_es_systems(harness)
    harness.romm.firmware_files = [dict(f) for f in _FIRMWARE]

    _, entries = await _compose(harness)

    assert len(entries) == 2
    fetches = [call for call in harness.romm.call_log if call[0] == "list_firmware"]
    assert len(fetches) == 1
