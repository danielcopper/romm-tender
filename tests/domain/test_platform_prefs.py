"""Tests for domain.platform_prefs — the per-platform sync toggles kept by fs_slug, and their move from RomM's ids.

Hand-enumerated cases; the property-based companion lives in ``test_platform_prefs_property.py``.
"""

from __future__ import annotations

import pytest

from domain.platform_prefs import platform_sync_enabled, resolve_sync_enabled, switches_by_folder


class TestResolveSyncEnabled:
    """resolve_sync_enabled — absent-id default depends on whether prefs exist."""

    def test_absent_id_defaults_true_when_map_empty(self):
        assert resolve_sync_enabled({}, "1") is True

    def test_absent_id_defaults_false_when_map_non_empty(self):
        # The #1007 bug condition: one explicit entry means every absent id is
        # disabled — so a complete map can be read literally.
        assert resolve_sync_enabled({"1": True}, "2") is False

    def test_present_id_true_is_returned(self):
        assert resolve_sync_enabled({"1": True}, "1") is True

    def test_present_id_false_is_returned(self):
        assert resolve_sync_enabled({"1": False}, "1") is False

    def test_present_false_id_returned_even_when_others_present(self):
        assert resolve_sync_enabled({"1": False, "2": True}, "1") is False


def _listed(platform_id: int, fs_slug: str | None, slug: str = "snes") -> dict[str, object]:
    return {"id": platform_id, "slug": slug, "fs_slug": fs_slug}


class TestPlatformSyncEnabled:
    """platform_sync_enabled — a platform is synced only where its own toggle, kept by fs_slug, is on."""

    def test_a_toggle_switched_on_is_synced(self):
        assert platform_sync_enabled({"snes": True}, "snes") is True

    def test_a_toggle_switched_off_is_not_synced(self):
        assert platform_sync_enabled({"snes": False}, "snes") is False

    def test_no_toggle_stored_at_all_is_not_synced(self):
        """#2452 D24: a fresh setup starts with every platform off."""
        assert platform_sync_enabled({}, "snes") is False

    def test_a_platform_without_a_toggle_of_its_own_is_not_synced(self):
        assert platform_sync_enabled({"snes": True}, "snes-hacks") is False

    def test_a_platform_without_an_fs_slug_is_never_synced(self):
        """#2452 D26."""
        assert platform_sync_enabled({"snes": True}, None) is False

    def test_a_value_that_is_not_true_is_not_synced(self):
        assert platform_sync_enabled({"snes": "yes"}, "snes") is False


class TestSwitchesByFolder:
    """switches_by_folder — the toggles kept by RomM id moved onto the listed platforms' fs_slug (#2452 D8)."""

    def test_each_id_moves_to_its_platform_s_folder(self):
        moved = switches_by_folder({"1": False, "2": True}, {}, [_listed(1, "snes"), _listed(2, "snes-hacks")])

        assert moved == {"snes": False, "snes-hacks": True}

    def test_an_id_the_listing_does_not_name_is_dropped(self):
        """#2452 D21."""
        moved = switches_by_folder({"1": True, "9": True}, {}, [_listed(1, "snes")])

        assert moved == {"snes": True}

    def test_a_listed_platform_the_old_map_did_not_name_keeps_its_state_off(self):
        """#2452 D22: once a toggle was saved, a platform without one was off, and stays off."""
        moved = switches_by_folder({"1": True}, {}, [_listed(1, "snes"), _listed(2, "snes-hacks")])

        assert moved == {"snes": True, "snes-hacks": False}

    def test_a_map_never_touched_switches_every_listed_platform_on(self):
        """#2452 D24: an existing installation whose toggles were never touched had every platform on."""
        moved = switches_by_folder({}, {}, [_listed(1, "snes"), _listed(2, "snes-hacks"), _listed(3, "gba", "gba")])

        assert moved == {"snes": True, "snes-hacks": True, "gba": True}

    def test_a_folder_that_already_has_a_toggle_keeps_it(self):
        moved = switches_by_folder({"1": True}, {"snes": False}, [_listed(1, "snes")])

        assert moved == {"snes": False}

    def test_a_toggle_kept_for_a_folder_not_listed_stays(self):
        moved = switches_by_folder({"1": True}, {"gba": True}, [_listed(1, "snes")])

        assert moved == {"gba": True, "snes": True}

    def test_a_listed_platform_without_an_fs_slug_gets_no_toggle(self):
        """#2452 D26."""
        moved = switches_by_folder({}, {}, [_listed(1, "snes"), _listed(2, None), _listed(3, "  ")])

        assert moved == {"snes": True}

    @pytest.mark.parametrize("platforms", [[], [_listed(1, None)]])
    def test_a_listing_naming_no_folder_moves_nothing_yet(self, platforms):
        assert switches_by_folder({"1": True}, {}, platforms) is None

    def test_the_maps_it_is_handed_are_left_as_they_were(self):
        by_id = {"1": True}
        switches = {"gba": False}

        switches_by_folder(by_id, switches, [_listed(1, "snes")])

        assert by_id == {"1": True}
        assert switches == {"gba": False}
