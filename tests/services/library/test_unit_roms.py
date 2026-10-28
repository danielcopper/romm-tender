"""Tests for services/library/_unit_roms.py — the preview union keyed as the apply's units key it (#2646)."""

from __future__ import annotations

from typing import Any

from services.library._unit_roms import stamp_preview_group_keys


def _rom(rom_id: int, *, siblings: tuple[int, ...] = (), key: str | None = None, **ids: int) -> dict[str, Any]:
    rom: dict[str, Any] = {
        "id": rom_id,
        "platform_id": 2,
        "platform_slug": "gba",
        "sibling_roms": [{"id": s} for s in siblings],
        **ids,
    }
    if key is not None:
        rom["sibling_group_key"] = key
    return rom


class TestStampPreviewGroupKeys:
    def test_a_version_its_listing_left_out_is_keyed_after_the_listed_ones(self):
        # GBA's listing leaves 30 out; a collection delivers it. GBA's unit keys
        # 31 against 30's stored key, and the collection's 30 against 31's.
        listed, left_out = _rom(31, ss_id=5, siblings=(30,)), _rom(30, igdb_id=9, ss_id=5, siblings=(31,))

        followed = stamp_preview_group_keys(
            [listed, left_out],
            {30: "ss:5:2", 31: "ss:5:2"},
            listed_rom_ids={31},
            unfetched_bound_ids={30},
        )

        assert (followed, listed["sibling_group_key"], left_out["sibling_group_key"]) == ({}, "ss:5:2", "igdb:9:2")

    def test_a_stored_row_a_skip_rebuilt_takes_the_key_it_follows(self):
        listed, rebuilt = _rom(31, igdb_id=300, ss_id=7, siblings=(30,)), _rom(30, key="ss:7:2")

        followed = stamp_preview_group_keys(
            [listed, rebuilt],
            {30: "ss:7:2", 31: "ss:7:2"},
            listed_rom_ids={31},
            unfetched_bound_ids={30},
        )

        assert (followed, rebuilt["sibling_group_key"]) == ({30: "igdb:300:2"}, "igdb:300:2")

    def test_a_version_its_listing_left_out_is_keyed_against_the_keys_the_listed_ones_take(self):
        listed, left_out = _rom(31, igdb_id=300, ss_id=5, siblings=(30,)), _rom(30, ss_id=5, siblings=(31,))

        stamp_preview_group_keys(
            [listed, left_out],
            {30: "ss:5:2", 31: "ss:5:2"},
            listed_rom_ids={31},
            unfetched_bound_ids={30},
        )

        assert (listed["sibling_group_key"], left_out["sibling_group_key"]) == ("igdb:300:2", "igdb:300:2")

    def test_roms_no_listing_returned_are_keyed_together(self):
        # Two versions of a game only a collection reaches: the apply's
        # collection pass keys every delivery at once.
        a, b = _rom(40, ss_id=5, siblings=(41,)), _rom(41, igdb_id=9, siblings=(40,))

        stamp_preview_group_keys([a, b], {}, listed_rom_ids=set(), unfetched_bound_ids=())

        assert (a["sibling_group_key"], b["sibling_group_key"]) == ("igdb:9:2", "igdb:9:2")
