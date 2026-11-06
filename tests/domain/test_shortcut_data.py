"""Tests for domain/shortcut_data.py pure functions."""

import os

import pytest

from domain.shortcut_data import (
    build_shortcuts_data,
    select_shortcuts_to_relocate,
    shortcut_launch_options,
)

# The launcher's home in the user's bin root: what the composition root hands
# the builder.
_LAUNCHER = "/home/deck/.local/bin/tender-rom-launcher"


class TestBuildShortcutsData:
    """Tests for build_shortcuts_data()."""

    def test_builds_correct_format(self):
        roms = [
            {
                "id": 1,
                "name": "Game A",
                "fs_name": "gamea.z64",
                "platform_name": "N64",
                "platform_slug": "n64",
                "igdb_id": 100,
                "sgdb_id": 200,
                "ra_id": 300,
            },
            {"id": 2, "name": "Game B", "platform_name": "SNES", "platform_slug": "snes"},
        ]
        result = build_shortcuts_data(roms, _LAUNCHER)
        assert len(result) == 2
        assert result[0]["rom_id"] == 1
        assert result[0]["name"] == "Game A"
        assert result[0]["fs_name"] == "gamea.z64"
        assert result[0]["platform_name"] == "N64"
        assert result[0]["platform_slug"] == "n64"
        assert result[0]["igdb_id"] == 100
        assert result[0]["sgdb_id"] == 200
        assert result[0]["ra_id"] == 300
        assert result[0]["cover_path"] == ""
        assert result[0]["exe"] == _LAUNCHER
        assert result[0]["start_dir"] == os.path.dirname(_LAUNCHER)
        assert result[1]["fs_name"] == ""

    def test_a_built_shortcut_carries_no_launch_command(self):
        # What starts a game is asked at each start; the launch options are the
        # appId Steam assigns, which no build can know (#2221 D7, D10).
        roms = [{"id": 1, "name": "Installed"}, {"id": 2, "name": "Not installed"}]
        result = build_shortcuts_data(roms, _LAUNCHER)
        assert all("launch_options" not in item for item in result)

    def test_empty_roms(self):
        result = build_shortcuts_data([], "/some/dir")
        assert result == []

    def test_missing_optional_fields(self):
        roms = [{"id": 5, "name": "Minimal"}]
        result = build_shortcuts_data(roms, _LAUNCHER)
        assert result[0]["rom_id"] == 5
        assert result[0]["platform_name"] == "Unknown"
        assert result[0]["platform_slug"] == ""
        assert result[0]["igdb_id"] is None
        assert result[0]["sgdb_id"] is None

    def test_carries_fs_size_bytes_from_raw_rom(self):
        # The server-reported size (#1395) rides the built dict onto the commit.
        roms = [{"id": 1, "name": "Game A", "fs_size_bytes": 3_145_728}]
        result = build_shortcuts_data(roms, _LAUNCHER)
        assert result[0]["fs_size_bytes"] == 3_145_728

    def test_carries_the_folder_romm_keeps_the_platform_in(self):
        """The commit records it on the Rom; the game page's BIOS tab reads the store by it (#2223 D45)."""
        roms = [{"id": 1, "name": "Game A", "platform_slug": "ngc", "platform_fs_slug": "gc"}]
        result = build_shortcuts_data(roms, _LAUNCHER)
        assert result[0]["platform_fs_slug"] == "gc"

    def test_missing_fs_size_bytes_is_none(self):
        # A raw ROM without the key builds None — "size unknown".
        roms = [{"id": 1, "name": "Game A"}]
        result = build_shortcuts_data(roms, _LAUNCHER)
        assert result[0]["fs_size_bytes"] is None

    @pytest.mark.parametrize(
        ("served", "expected"),
        [
            ({"has_file_on_disk": False, "missing_from_fs": True}, False),
            ({"has_file_on_disk": False, "is_physical": True}, False),
            ({"has_file_on_disk": True}, True),
            ({}, True),
        ],
    )
    def test_carries_whether_romm_has_a_file(self, served, expected):
        roms = [{"id": 1, "name": "Game A", **served}]
        result = build_shortcuts_data(roms, _LAUNCHER)
        assert result[0]["has_file_on_disk"] is expected

    def test_exe_path_contains_rom_launcher(self):
        roms = [{"id": 1, "name": "Game"}]
        result = build_shortcuts_data(roms, _LAUNCHER)
        assert result[0]["exe"].endswith("/bin/tender-rom-launcher")

    def test_start_dir_is_parent_of_exe(self):
        roms = [{"id": 1, "name": "Game"}]
        result = build_shortcuts_data(roms, _LAUNCHER)
        assert result[0]["start_dir"] == os.path.dirname(result[0]["exe"])

    def test_multiple_roms_each_has_required_fields(self):
        required_fields = {"rom_id", "name", "exe", "start_dir", "platform_name", "platform_slug"}
        roms = [{"id": i, "name": f"Game {i}"} for i in range(5)]
        result = build_shortcuts_data(roms, _LAUNCHER)
        for item in result:
            for field in required_fields:
                assert field in item, f"Missing field '{field}' in shortcut data"


class TestBuildShortcutsDataVersionMetadata:
    """build_shortcuts_data derives the sibling-group key + version dimensions (#1295)."""

    def test_carries_group_key_and_version_dimensions(self):
        roms = [
            {
                "id": 1,
                "name": "Game A",
                "platform_id": 57,
                "igdb_id": 3404,
                "regions": ["USA", "Europe"],
                "languages": ["En", "Fr"],
                "revision": "1",
                "tags": ["Demo"],
                "rom_user": {"is_main_sibling": True},
            }
        ]
        result = build_shortcuts_data(roms, _LAUNCHER)
        assert result[0]["sibling_group_key"] == "igdb:3404:57"
        assert result[0]["regions"] == ["USA", "Europe"]
        assert result[0]["languages"] == ["En", "Fr"]
        assert result[0]["revision"] == "1"
        assert result[0]["tags"] == ["Demo"]
        assert result[0]["is_main_sibling"] is True

    def test_unmatched_rom_gets_fallback_group_key(self):
        roms = [{"id": 4409, "name": "Solo", "platform_id": 57}]
        result = build_shortcuts_data(roms, _LAUNCHER)
        assert result[0]["sibling_group_key"] == "romm:4409:57"

    def test_missing_version_fields_default_empty(self):
        # A ROM with no version metadata at all: empty arrays, blank revision,
        # is_main_sibling False (rom_user absent → the `or {}` guard).
        roms = [{"id": 5, "name": "Minimal", "platform_id": 9}]
        result = build_shortcuts_data(roms, _LAUNCHER)
        assert result[0]["regions"] == []
        assert result[0]["languages"] == []
        assert result[0]["revision"] == ""
        assert result[0]["tags"] == []
        assert result[0]["is_main_sibling"] is False

    def test_null_rom_user_is_not_main_sibling(self):
        # Defensive guard: a missing or null rom_user (whatever the server
        # schema promises) must degrade to False without raising.
        roms = [{"id": 6, "name": "Untouched", "platform_id": 9, "rom_user": None}]
        result = build_shortcuts_data(roms, _LAUNCHER)
        assert result[0]["is_main_sibling"] is False

    def test_null_version_arrays_default_empty(self):
        # RomM can send explicit nulls for the array dimensions.
        roms = [
            {
                "id": 7,
                "name": "Nulls",
                "platform_id": 9,
                "regions": None,
                "languages": None,
                "tags": None,
                "revision": None,
            }
        ]
        result = build_shortcuts_data(roms, _LAUNCHER)
        assert result[0]["regions"] == []
        assert result[0]["languages"] == []
        assert result[0]["tags"] == []
        assert result[0]["revision"] == ""

    def test_persisted_group_key_is_authoritative_over_recompute(self):
        # #1296: an incremental-skip reconstructed row carries the persisted
        # sibling_group_key but NO platform_id — recomputing it would yield
        # "igdb:100:None" and split the group's bucket from a freshly-fetched
        # sibling that DOES carry platform_id. The persisted key must win verbatim.
        reconstructed = {"id": 10, "name": "Zelda (USA)", "igdb_id": 100, "sibling_group_key": "igdb:100:57"}
        result = build_shortcuts_data([reconstructed], _LAUNCHER)
        assert result[0]["sibling_group_key"] == "igdb:100:57"

    def test_reconstructed_and_fetched_sibling_land_in_one_bucket(self):
        # The end-to-end #1296 regression: a reconstructed representative (persisted
        # key, no platform_id) and a freshly fetched sibling of the same game
        # (platform_id + igdb_id, no key) must resolve to the SAME group key so the
        # preview collapse buckets them as ONE game — not a phantom "new".
        from domain.sync_diff import collapse_sibling_groups

        reconstructed = {"id": 10, "name": "Zelda (USA)", "sibling_group_key": "igdb:100:57"}
        fetched = {"id": 11, "name": "Zelda (JP)", "igdb_id": 100, "platform_id": 57}
        result = build_shortcuts_data([reconstructed, fetched], _LAUNCHER)
        assert {sd["sibling_group_key"] for sd in result} == {"igdb:100:57"}

        emitted = collapse_sibling_groups(result, registry={}, installed_rom_ids=set(), complete_group_view=True)
        assert len(emitted) == 1  # one bucket → one representative


class TestShortcutLaunchOptions:
    def test_a_shortcut_carries_its_own_app_id(self):
        assert shortcut_launch_options(3000000001) == "3000000001"

    def test_the_launch_options_are_one_word_no_shell_changes(self):
        # Steam hands them to a shell; digits alone are what reaches the launcher.
        assert shortcut_launch_options(42).isdigit()


class TestSelectShortcutsToRelocate:
    """Which live shortcuts still have to be pointed at the launcher's home."""

    # Ours by its ending, wherever it was written.
    _ELSEWHERE = "/home/deck/.local/opt/bin/tender-rom-launcher"
    _ELSEWHERE_TOO = "/opt/tender/bin/tender-rom-launcher"

    # Not ours: the ending an earlier version of this program wrote. Ownership
    # is one ending, so this is a foreign shortcut and is never selected.
    _SUPERSEDED = "/home/deck/.local/share/romm-tender/bin/rom-launcher"

    def test_picks_a_shortcut_of_ours_wherever_it_was_written(self):
        exes = {10: self._ELSEWHERE, 20: self._ELSEWHERE_TOO}

        assert select_shortcuts_to_relocate(exes, _LAUNCHER) == [10, 20]

    def test_never_picks_a_shortcut_on_the_superseded_ending(self):
        """One ending is the whole of ownership — an earlier version's is not it."""
        assert select_shortcuts_to_relocate({10: self._SUPERSEDED}, _LAUNCHER) == []

    def test_skips_a_shortcut_already_on_the_launchers_home(self):
        assert select_shortcuts_to_relocate({10: _LAUNCHER, 20: self._ELSEWHERE}, _LAUNCHER) == [20]

    def test_never_picks_a_shortcut_that_is_not_ours(self):
        assert select_shortcuts_to_relocate({10: "/usr/bin/some-other-game"}, _LAUNCHER) == []

    def test_a_quoted_exe_is_still_ours(self):
        """AddShortcut stores it bare; reading a hand-quoted one as foreign strands it."""
        assert select_shortcuts_to_relocate({10: f'"{self._ELSEWHERE}"'}, _LAUNCHER) == [10]

    def test_a_quoted_exe_already_on_the_home_is_still_skipped(self):
        assert select_shortcuts_to_relocate({10: f'"{_LAUNCHER}"'}, _LAUNCHER) == []

    def test_the_answer_is_sorted(self):
        assert select_shortcuts_to_relocate(
            {30: self._ELSEWHERE, 10: self._ELSEWHERE, 20: self._ELSEWHERE}, _LAUNCHER
        ) == [10, 20, 30]

    def test_an_empty_reading_selects_nothing(self):
        assert select_shortcuts_to_relocate({}, _LAUNCHER) == []
