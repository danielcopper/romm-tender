"""Tests for the two roots the plugin's data lives under, and the launcher beneath one."""

from __future__ import annotations

from domain.user_data_location import APP_DIR_NAME, config_root, data_root, launcher_path


class TestTheDirectoryName:
    def test_both_roots_are_named_after_it(self):
        assert config_root("/home/deck").endswith(f"/{APP_DIR_NAME}")
        assert data_root("/home/deck").endswith(f"/{APP_DIR_NAME}")


class TestRoots:
    def test_config_root_is_under_the_users_own_home(self):
        assert config_root("/home/deck") == "/home/deck/.config/romm-tender"

    def test_data_root_is_under_the_users_own_home(self):
        assert data_root("/home/deck") == "/home/deck/.local/share/romm-tender"


class TestLauncherPath:
    def test_the_launcher_sits_under_the_root_it_is_given(self):
        assert launcher_path("/home/deck/.local/share/romm-tender") == (
            "/home/deck/.local/share/romm-tender/bin/rom-launcher"
        )

    def test_the_same_two_components_name_the_copy_the_release_ships(self):
        assert launcher_path("/home/deck/homebrew/plugins/romm-tender") == (
            "/home/deck/homebrew/plugins/romm-tender/bin/rom-launcher"
        )

    def test_it_ends_in_the_suffix_shortcut_ownership_is_read_off(self):
        """``frontend/src/utils/steamShortcuts.ts`` and
        ``services/prune/requests.py`` match this suffix.

        A launcher moved to a root whose last two components are anything else
        would leave every shortcut written before the move unrecognised as ours
        — invisible here, because nothing else in this repository composes the
        path a second time.
        """
        assert launcher_path("/anywhere/at/all").endswith("/bin/rom-launcher")
