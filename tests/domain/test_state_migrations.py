"""Tests for domain/state_migrations.py — pure migration functions."""

import pytest

from domain.state_migrations import OLDEST_SETTINGS_VERSION, migrate_settings, unreadable_settings


class TestUnreadableSettings:
    """Which settings files this release reads, and what it names about the rest."""

    def test_the_oldest_version_it_reads_is_13(self):
        assert OLDEST_SETTINGS_VERSION == 13

    @pytest.mark.parametrize("version", [13, 14, 99])
    def test_a_whole_version_of_13_or_newer_is_read(self, version):
        assert unreadable_settings({"version": version, "romm_url": "https://romm.example"}) is None

    @pytest.mark.parametrize(
        ("data", "answer"),
        [
            ({"version": 12}, "its version 12 is older than 13"),
            ({"version": 0}, "its version 0 is older than 13"),
            ({"version": -1}, "its version -1 is older than 13"),
            ({"romm_url": "https://romm.example"}, "it has no version"),
            ({}, "it has no version"),
            ({"version": "13"}, "its version is a JSON string, not a whole number"),
            ({"version": None}, "its version is a JSON null, not a whole number"),
            ({"version": True}, "its version is a JSON boolean, not a whole number"),
            ({"version": 13.0}, "its version is a JSON number, not a whole number"),
            ({"version": [13]}, "its version is a JSON array, not a whole number"),
            ({"version": {"v": 13}}, "its version is a JSON object, not a whole number"),
            ({"version": "13\nforged line"}, "its version is a JSON string, not a whole number"),
            ([], "it holds a JSON array, not an object"),
            ("settings", "it holds a JSON string, not an object"),
            (13, "it holds a JSON number, not an object"),
            (False, "it holds a JSON boolean, not an object"),
            (None, "it holds a JSON null, not an object"),
        ],
    )
    def test_anything_else_is_not_read_and_the_answer_names_what_was_found(self, data, answer):
        assert unreadable_settings(data) == answer


class TestMigrateSettings:
    def test_a_file_at_version_13_gets_each_pick_as_retrodecks_and_keeps_the_rest(self):
        data = {
            "version": 13,
            "romm_url": "http://example.com",
            "log_level": "warn",
            "romm_api_token": "rmm_existing",
            "romm_api_token_id": 7,
            "platform_cores": {"snes": "bsnes"},
            "romm_api_token_origin": "http://example.com",
            "romm_api_token_source": "minted",
            "enabled_collections": {"standard": {"1": True}, "smart": {}, "virtual": {}},
        }
        assert migrate_settings(data) == {
            **data,
            "version": 15,
            "platform_cores": {"snes": {"label": "bsnes", "source": "retrodeck"}},
            "enabled_platforms": {},
        }

    def test_a_value_that_names_no_pick_is_dropped(self):
        data = {"version": 13, "platform_cores": {"snes": "bsnes", "gba": "", "nes": 7}}
        assert migrate_settings(data)["platform_cores"] == {"snes": {"label": "bsnes", "source": "retrodeck"}}

    def test_a_file_at_version_15_is_returned_unchanged(self):
        data = {"version": 15, "platform_cores": {"snes": {"label": "bsnes", "source": "retrodeck"}}}
        assert migrate_settings(data) == data

    def test_running_it_twice_changes_nothing_more(self):
        data = {"version": 13, "platform_cores": {"snes": "bsnes"}}
        once = migrate_settings(data)
        assert migrate_settings(once) == once

    def test_the_callers_dict_is_not_the_one_returned(self):
        data = {"version": 13, "romm_url": "http://example.com"}
        result = migrate_settings(data)
        result["romm_url"] = "http://other.example"
        assert data == {"version": 13, "romm_url": "http://example.com"}


class TestTheTogglesKeptByRommId:
    """Version 15 keeps the per-platform sync toggles by fs_slug; those kept by RomM id wait (#2452 D8, D24)."""

    @pytest.mark.parametrize("version", [13, 14])
    def test_the_toggles_kept_by_id_move_aside_and_the_new_map_starts_empty(self, version):
        data = {"version": version, "enabled_platforms": {"1": True, "2": False}}

        migrated = migrate_settings(data)

        assert migrated["version"] == 15
        assert migrated["enabled_platform_ids"] == {"1": True, "2": False}
        assert migrated["enabled_platforms"] == {}

    def test_a_map_never_touched_moves_aside_too(self):
        """An empty map meant every platform on, which the move keeps (#2452 D24)."""
        migrated = migrate_settings({"version": 14, "enabled_platforms": {}})

        assert migrated["enabled_platform_ids"] == {}
        assert migrated["enabled_platforms"] == {}

    @pytest.mark.parametrize("value", [None, [], "1", 7])
    def test_a_value_that_is_no_map_names_no_toggle_and_is_dropped(self, value):
        migrated = migrate_settings({"version": 14, "enabled_platforms": value})

        assert "enabled_platform_ids" not in migrated
        assert migrated["enabled_platforms"] == {}

    def test_a_file_at_version_15_keeps_its_toggles(self):
        data = {"version": 15, "enabled_platforms": {"snes": True}}

        assert migrate_settings(data) == data

    def test_running_it_twice_moves_nothing_more(self):
        once = migrate_settings({"version": 14, "enabled_platforms": {"1": True}})

        assert migrate_settings(once) == once
