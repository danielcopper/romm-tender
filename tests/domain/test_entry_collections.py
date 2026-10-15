"""Tests for domain/entry_collections.py — which collections a synced shortcut is filed in."""

from __future__ import annotations

from typing import Any

from domain.entry_collections import RunCollections, platform_collection_name
from domain.sync_diff import BIND_ROM_ID_KEY
from domain.work_unit import WorkUnit


def _platform(slug: str, name: str) -> WorkUnit:
    return WorkUnit(type="platform", id=1, name=name, slug=slug, rom_count=0)


def _collection(
    cid: str,
    name: str,
    members: tuple[int, ...],
    *,
    kind: str = "standard",
    virtual_type: str | None = None,
    is_own: bool = True,
) -> WorkUnit:
    return WorkUnit(
        type="collection",
        id=cid,
        name=name,
        slug="",
        rom_count=len(members),
        collection_kind=kind,  # type: ignore[arg-type]
        virtual_type=virtual_type,
        member_rom_ids=members,
        is_own=is_own,
    )


def _run(units: list[WorkUnit], *, naming_mode: str = "merge", create_platform_groups: bool = False) -> RunCollections:
    return RunCollections.at_run_start(
        units,
        platform_names={"n64": "Nintendo 64"},
        naming_mode=naming_mode,
        create_platform_groups=create_platform_groups,
    )


def _entry(rom_id: int, *, group: str | None = None, slug: str = "n64", **extra: Any) -> dict[str, Any]:
    return {"rom_id": rom_id, "sibling_group_key": group, "platform_slug": slug, **extra}


class TestPlatformCollectionName:
    def test_a_rom_a_platform_unit_fetched_is_filed_under_the_runs_display_name(self):
        assert platform_collection_name(1, "n64", {1}, {"n64": "Nintendo 64"}, False) == "Nintendo 64"

    def test_a_rom_no_platform_unit_fetched_is_filed_nowhere_with_platform_groups_off(self):
        assert platform_collection_name(30, "gba", {1}, {"n64": "Nintendo 64"}, False) is None

    def test_with_platform_groups_on_it_is_filed_under_the_slug_where_the_run_has_no_name(self):
        assert platform_collection_name(30, "gba", {1}, {"n64": "Nintendo 64"}, True) == "gba"

    def test_no_platform_tracking_files_every_rom(self):
        assert platform_collection_name(30, "n64", None, {"n64": "Nintendo 64"}, False) == "Nintendo 64"


class TestAtRunStart:
    def test_indexes_each_listed_member_under_its_collections_key(self):
        run = _run(
            [_platform("n64", "Nintendo 64"), _collection("7", "Favorites", (1, 2)), _collection("8", "RPGs", (2,))]
        )

        assert dict(run.romm_keys_by_rom) == {1: ("Favorites",), 2: ("Favorites", "RPGs")}

    def test_leaves_out_a_foreign_smart_collection(self):
        run = _run([_collection("5", "Their Filter", (1,), kind="smart", is_own=False)])

        assert dict(run.romm_keys_by_rom) == {}

    def test_keeps_an_own_smart_and_a_foreign_standard_collection(self):
        run = _run(
            [
                _collection("5", "My Filter", (1,), kind="smart"),
                _collection("9", "Their Faves", (2,), is_own=False),
            ]
        )

        assert dict(run.romm_keys_by_rom) == {1: ("My Filter",), 2: ("Their Faves",)}

    def test_by_label_appends_the_type_label_to_the_key(self):
        run = _run(
            [
                _collection("5", "Mario", (1,), kind="smart"),
                _collection("v1", "Mario", (1,), kind="virtual", virtual_type="franchise"),
            ],
            naming_mode="by_label",
        )

        assert run.romm_keys_by_rom[1] == ("Mario (Smart)", "Mario (Franchise)")

    def test_merge_unions_same_named_collections_into_one_key(self):
        run = _run([_collection("5", "Mario", (1,), kind="smart"), _collection("7", "Mario", (1, 2))])

        assert dict(run.romm_keys_by_rom) == {1: ("Mario",), 2: ("Mario",)}

    def test_keys_that_fold_together_take_the_spelling_first_in_queue_order(self):
        run = _run([_collection("7", "7 Up", (1,)), _collection("8", "7 up", (2,))])

        assert dict(run.romm_keys_by_rom) == {1: ("7 Up",), 2: ("7 Up",)}


class TestFileEntries:
    def test_names_the_platform_collection_and_the_romm_keys(self):
        run = _run([_collection("7", "Favorites", (1,))])

        filed = run.file_entries([_entry(1), _entry(2)], [_entry(1), _entry(2)], {1, 2})

        assert [(e["platform_collection"], e["romm_collections"]) for e in filed] == [
            ("Nintendo 64", ["Favorites"]),
            ("Nintendo 64", []),
        ]

    def test_a_game_is_in_a_collection_listing_any_of_its_versions(self):
        run = _run([_collection("7", "Favorites", (11,)), _collection("8", "RPGs", (10,))])
        built = [_entry(10, group="g"), _entry(11, group="g"), _entry(12, group="other")]

        filed = run.file_entries([_entry(10, group="g"), _entry(12, group="other")], built, {10, 11, 12})

        assert [e["romm_collections"] for e in filed] == [["RPGs", "Favorites"], []]

    def test_an_entry_without_a_group_key_is_judged_by_its_own_rom_alone(self):
        run = _run([_collection("7", "Favorites", (11,))])
        built = [_entry(10), _entry(11)]

        filed = run.file_entries([_entry(10)], built, {10, 11})

        assert filed[0]["romm_collections"] == []

    def test_a_rebind_entry_is_judged_by_the_rom_it_binds_onto(self):
        run = _run([_collection("7", "Favorites", (20,))], create_platform_groups=False)
        rebind = {**_entry(99), BIND_ROM_ID_KEY: 20}

        filed = run.file_entries([rebind], [_entry(20)], {20})

        assert (filed[0]["platform_collection"], filed[0]["romm_collections"]) == ("Nintendo 64", ["Favorites"])

    def test_a_collection_only_rom_names_no_platform_collection_with_platform_groups_off(self):
        run = _run([_collection("7", "Favorites", (30,))])

        filed = run.file_entries([_entry(30, slug="gba")], [_entry(30, slug="gba")], {1})

        assert (filed[0]["platform_collection"], filed[0]["romm_collections"]) == (None, ["Favorites"])

    def test_returns_copies_and_leaves_the_source_entries_alone(self):
        run = _run([])
        source = _entry(1, cover_path="/cache/1.png")

        filed = run.file_entries([source], [source], {1})

        assert filed[0] is not source
        assert filed[0]["cover_path"] == "/cache/1.png"
        assert "platform_collection" not in source
