"""Tests for the launcher's two paths."""

from __future__ import annotations

from domain.user_data_location import (
    LAUNCHER_EXE_SUFFIX,
    launcher_in_bin_dir,
    launcher_path,
)


class TestLauncherPath:
    def test_it_names_the_copy_the_release_ships(self):
        assert launcher_path("/home/deck/.local/lib/romm-tender") == (
            "/home/deck/.local/lib/romm-tender/bin/tender-rom-launcher"
        )

    def test_it_ends_in_the_suffix_shortcut_ownership_is_read_off(self):
        """``frontend/src/utils/steamShortcuts.ts`` and
        ``services/prune/requests.py`` match this suffix.

        A launcher moved to a root whose last two components are anything else
        would leave every shortcut written before the move unrecognised as ours
        — invisible here, because nothing else in this repository composes the
        path a second time.
        """
        assert launcher_path("/anywhere/at/all").endswith(LAUNCHER_EXE_SUFFIX)
        assert LAUNCHER_EXE_SUFFIX == "/bin/tender-rom-launcher"


class TestLauncherInBinDir:
    def test_the_launcher_goes_straight_into_the_bin_directory(self):
        assert launcher_in_bin_dir("/home/deck/.local/bin") == "/home/deck/.local/bin/tender-rom-launcher"

    def test_the_installed_path_carries_the_ownership_suffix_too(self):
        """The two composers have to agree, or an installed shortcut is foreign to us."""
        assert launcher_in_bin_dir("/home/deck/.local/bin").endswith(LAUNCHER_EXE_SUFFIX)

    def test_a_bin_dir_not_called_bin_loses_the_suffix(self):
        """Edge: the constraint the value carries, which nothing can enforce here."""
        assert not launcher_in_bin_dir("/home/deck/.local/programs").endswith(LAUNCHER_EXE_SUFFIX)
