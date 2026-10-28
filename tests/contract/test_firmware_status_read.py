"""Contract tests for the Library page's BIOS read — the calls that make it.

The page lists RomM's platforms (`get_platforms`), asks `get_firmware_status`
whether RomM's firmware listing could be read, and then
`get_platform_firmware_status` for every listed platform, one at a time, because
a platform's state costs a live per-system reading. What the reader ends up
looking at is those joined, and the join is the thing nothing else pins: the
service tests state each half on its own, and the ~25 that describe a pane's
answer ask through a local helper, which would reproduce a composition bug
rather than catch one.

So this tier assembles the way the frontend does — the list, the overview, then
one per-platform call per listed platform — and holds the result against what
the single whole-page call answered before the split. The key sets below are
read off that implementation (`a5777746`), not off the code under test, which is
what makes them an oracle rather than a restatement.

No BIOS root exists under the harness's `tmp_path`, so the real resolver
answers unresolved for every system and no reading here is ever complete; the
states a stated reading produces are `tests/services/test_firmware.py`'s
(`TestOnePlatformsOwnEntry`).
"""

from __future__ import annotations

from typing import Any

from domain.platform_system import PLATFORM_IDS_KEY, PlatformIds, encode_platform_ids

from ._seed import seed_es_systems, seed_rom

_FIRMWARE = [
    ("dc", {"id": 1, "file_name": "dc_boot.bin", "file_size_bytes": 8, "md5_hash": "a"}),
    ("dc", {"id": 2, "file_name": "dc_flash.bin", "file_size_bytes": 4, "md5_hash": ""}),
    ("gba", {"id": 3, "file_name": "gba_bios.bin", "file_size_bytes": 16, "md5_hash": ""}),
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
    "emulator_data_reason",
    "emulator_source",
    "platform_system",
    "deletable_count",
    "server_count",
    "local_count",
    "required_count",
    "required_downloaded",
    "required_withheld",
    "bios_level",
    # Not in a5777746: the launching emulator's one-of groups, each one
    # requirement.
    "one_of_groups",
    # The pair the "n/N RomM library files" ratio states, over the rows whose
    # verdict was established.
    "ratio_server_count",
    "ratio_local_count",
    # Not in a5777746: what the resolver says about the launching emulator
    # — whether it was read, how it finds its firmware, the console's
    # need, and what keeps the verdict open. The verdict itself is
    # `bios_level`.
    "emulator_read",
    "locating",
    "emulator_declaration",
    "console_needs_image",
    "refused",
    "emulator_notes",
    # Not in a5777746 either: what the launching emulator's search of the BIOS
    # folder established, for an emulator that searches by content.
    "content_search",
    # Not in a5777746: one entry per emulator the resolver answered for.
    "emulator_tabs",
    # Why a download here lands in Tender's firmware store only, where the
    # replaced files go, and the platform's store files.
    "store_only",
    "backup_root",
    "store_files",
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
    # Not in a5777746: the ready sentence's optional tail counts the launching
    # emulator's own files, and this is the row field that says which they are.
    "used_by_active",
    # Not in a5777746: the row as an option of the launching emulator's group,
    # whether "Download required" fetches it, and a stale setting naming a file
    # in a declared folder.
    "one_of",
    "fetch_for_required",
    "missing_configured_image",
    # Not in a5777746: a core option naming a file in a declared folder that
    # the core does not offer (#2644 D7).
    "configured_image_not_offered",
    "supplied_by",
    "satisfied",
    "declared_kind",
    "declaration",
    "checked",
    "caveats",
    "images",
    "deletable_count",
    # Not in a5777746: the emulator whose requirement the row is, which a
    # surface shows it with.
    "declared_by",
    # The library file that fills the row and how, Tender's store copy, what
    # lies at the place and whose link it is, why no link may go there, whether
    # "Replace…" is offered, the declared folder a file lies in, and the name a
    # Delete is asked by.
    "library",
    "store_file",
    "link",
    "linked_for",
    "place_refusal",
    "replace_offered",
    "in_folder",
    "delete_name",
    # The name the platform a link of Tender's serves the row for is shown under.
    "linked_for_name",
}


async def _compose(harness) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The page's whole answer, assembled the way the page assembles it.

    Returns the overview and the entries the list led to, one per listed
    platform. Asserts the join on the way through, because a platform that
    answers under a different slug than the one it was asked about is a
    composition failure rather than a result to carry on with.
    """
    listed = await harness.endpoints.get_platforms()
    assert listed["success"] is True
    overview = await harness.endpoints.get_firmware_status()
    entries: list[dict[str, Any]] = []
    for platform in listed["platforms"]:
        answer = await harness.endpoints.get_platform_firmware_status(platform["slug"])
        assert answer["success"] is True
        entry = answer["platform"]
        assert entry["platform_slug"] == platform["slug"]
        entries.append(entry)
    return overview, entries


def _list_platforms(harness, *slugs: str) -> None:
    """RomM's platforms, each holding a ROM so the page lists it."""
    harness.romm.platforms = [
        {"id": index, "name": slug, "slug": slug, "rom_count": 1} for index, slug in enumerate(slugs, start=1)
    ]


async def test_the_two_calls_compose_the_whole_page_answer(harness):
    """The overview plus per-platform answers rebuild the payload the page renders."""
    seed_es_systems(harness)
    seed_rom(harness, 1, platform_slug="gba")  # bound → has_games
    _list_platforms(harness, "dc", "gba", "n64")
    for platform_slug, row in _FIRMWARE:
        harness.romm.seed_firmware(platform_slug, dict(row))

    overview, entries = await _compose(harness)

    # The overview carries nothing about any platform; every listed platform
    # answers with the whole entry the pane reads — `n64` too, though the
    # library holds no firmware for it and no game of it is synced.
    assert overview == {"success": True, "server_offline": False}
    assert [e["platform_slug"] for e in entries] == ["dc", "gba", "n64"]
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
    assert entries[2]["files"] == []
    assert entries[2]["has_games"] is False
    assert gba["active_core"] == "mgba_libretro.so"


async def test_the_listing_is_fetched_once_for_the_whole_walk(harness):
    """One round trip for the library, however many platforms are asked about.

    The reading is per platform; what the RomM library holds is not. The listing
    cache is what keeps the second half true, and a walk that re-fetched it per
    platform would put a network call between every row and the next.
    """
    seed_es_systems(harness)
    _list_platforms(harness, "dc", "gba")
    for platform_slug, row in _FIRMWARE:
        harness.romm.seed_firmware(platform_slug, dict(row))
    # The platforms' ids were kept by an earlier reading, so the platform list
    # is read by the page's own list and by the firmware listing alone.
    with harness.uow_factory() as uow:
        uow.kv_config.set(PLATFORM_IDS_KEY, encode_platform_ids({"dc": PlatformIds(), "gba": PlatformIds()}))

    _, entries = await _compose(harness)

    assert len(entries) == 2
    fetches = [call for call in harness.romm.call_log if call[0] == "list_platforms"]
    assert len(fetches) == 2
