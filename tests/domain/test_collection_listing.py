"""Tests for domain.collection_listing.collection_entry."""

from __future__ import annotations

from domain.collection_listing import collection_entry

_ENABLED = {"standard": {"7": True}, "smart": {}, "virtual": {}}


class TestSharedFields:
    def test_derived_fields_with_the_callers_fields_merged_in(self):
        listing = {"id": 7, "name": "Faves", "rom_count": 3, "rom_ids": [1, 2, 3]}

        entry = collection_entry(listing, "standard", _ENABLED, None, is_favorite=True)

        assert entry == {
            "id": "7",
            "name": "Faves",
            "rom_count": 3,
            "sync_enabled": True,
            "kind": "standard",
            "is_favorite": True,
        }

    def test_sync_enabled_is_read_from_the_kinds_own_bucket(self):
        entry = collection_entry({"id": 7, "rom_ids": []}, "smart", _ENABLED, None)

        assert entry["sync_enabled"] is False

    def test_rom_count_falls_back_to_the_member_list(self):
        entry = collection_entry({"id": 7, "rom_ids": [1, 2]}, "standard", _ENABLED, None)

        assert entry["rom_count"] == 2
        assert entry["name"] == ""


class TestInSteamCount:
    def test_counts_the_members_in_the_reachable_set(self):
        listing = {"id": 7, "rom_ids": [1, 2, 3]}

        entry = collection_entry(listing, "standard", _ENABLED, {1, 3, 99})

        assert entry["in_steam_count"] == 2

    def test_two_versions_of_one_game_both_count(self):
        """Members, not shortcuts: two reachable versions sharing one shortcut count twice."""
        entry = collection_entry({"id": 7, "rom_ids": [1, 2]}, "standard", _ENABLED, {1, 2})

        assert entry["in_steam_count"] == 2

    def test_nothing_reachable_is_zero(self):
        entry = collection_entry({"id": 7, "rom_ids": [1, 2]}, "standard", _ENABLED, set())

        assert entry["in_steam_count"] == 0

    def test_absent_when_the_reachable_set_is_unknown(self):
        entry = collection_entry({"id": 7, "rom_ids": [1, 2]}, "standard", _ENABLED, None)

        assert "in_steam_count" not in entry

    def test_a_listing_without_member_ids_counts_zero(self):
        entry = collection_entry({"id": 7, "rom_count": 4}, "standard", _ENABLED, {1})

        assert entry["in_steam_count"] == 0
