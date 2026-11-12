"""Tests for domain.platform_prefs — the per-platform sync toggles kept by fs_slug, and their move from RomM's ids.

Hand-enumerated cases; the property-based companion lives in ``test_platform_prefs_property.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from domain.platform_prefs import (
    platform_sync_enabled,
    resolve_sync_enabled,
    stored_switches,
    switches_as_read,
    switches_by_folder,
    switches_kept_by_id,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


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

    def test_a_value_that_is_not_true_reads_as_off(self):
        assert resolve_sync_enabled({"1": "yes"}, "1") is False


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


def _moved(by_id: Mapping[str, Any], switches: Mapping[str, Any], platforms: list[dict[str, Any]]) -> dict[str, Any]:
    move = switches_by_folder(by_id, switches, platforms)
    assert move is not None
    return move.switches


class TestSwitchesByFolder:
    """switches_by_folder — the toggles kept by RomM id moved onto the listed platforms' fs_slug (#2452 D8)."""

    def test_each_id_moves_to_its_platform_s_folder(self):
        moved = _moved({"1": False, "2": True}, {}, [_listed(1, "snes"), _listed(2, "snes-hacks")])

        assert moved == {"snes": False, "snes-hacks": True}

    def test_an_id_the_listing_does_not_name_is_dropped(self):
        """#2452 D21."""
        moved = _moved({"1": True, "9": True}, {}, [_listed(1, "snes")])

        assert moved == {"snes": True}

    def test_a_listed_platform_the_old_map_did_not_name_keeps_its_state_off(self):
        """#2452 D22: once a toggle was saved, a platform without one was off, and stays off."""
        moved = _moved({"1": True}, {}, [_listed(1, "snes"), _listed(2, "snes-hacks")])

        assert moved == {"snes": True, "snes-hacks": False}

    def test_a_map_never_touched_switches_every_listed_platform_on(self):
        """#2452 D24: an existing installation whose toggles were never touched had every platform on."""
        moved = _moved({}, {}, [_listed(1, "snes"), _listed(2, "snes-hacks"), _listed(3, "gba", "gba")])

        assert moved == {"snes": True, "snes-hacks": True, "gba": True}

    def test_a_folder_that_already_has_a_toggle_keeps_it(self):
        moved = _moved({"1": True}, {"snes": False}, [_listed(1, "snes")])

        assert moved == {"snes": False}

    def test_a_toggle_kept_for_a_folder_not_listed_stays(self):
        moved = _moved({"1": True}, {"gba": True}, [_listed(1, "snes")])

        assert moved == {"gba": True, "snes": True}

    def test_a_listed_platform_without_an_fs_slug_gets_no_toggle(self):
        """#2452 D26."""
        moved = _moved({}, {}, [_listed(1, "snes"), _listed(2, None), _listed(3, "  ")])

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


class TestTheStoredToggles:
    """stored_switches / switches_kept_by_id — the one guarded read of each map in the settings."""

    def test_the_toggles_kept_by_folder_are_read(self):
        assert stored_switches({"enabled_platforms": {"snes": True}}) == {"snes": True}

    @pytest.mark.parametrize("value", [None, [], "snes", 7])
    def test_a_value_that_is_no_map_reads_as_no_toggle(self, value):
        assert stored_switches({"enabled_platforms": value}) == {}

    def test_settings_without_the_key_read_as_no_toggle(self):
        assert stored_switches({}) == {}

    def test_the_toggles_kept_by_id_are_read_while_they_wait(self):
        assert switches_kept_by_id({"enabled_platform_ids": {}}) == {}
        assert switches_kept_by_id({"enabled_platform_ids": {"1": True}}) == {"1": True}

    @pytest.mark.parametrize("settings", [{}, {"enabled_platform_ids": None}, {"enabled_platform_ids": [1]}])
    def test_none_wait_once_moved_or_never_kept(self, settings):
        assert switches_kept_by_id(settings) is None


class TestSwitchesAsRead:
    """switches_as_read — a reader goes by the map the move would write while one is pending (#2452 D27)."""

    def test_once_moved_the_toggles_are_read_as_they_stand(self):
        switches = {"snes": True}

        assert switches_as_read(switches, None, [_listed(1, "snes"), _listed(2, "snes-hacks")]) is switches

    def test_while_kept_by_id_each_platform_is_read_by_the_rule_its_id_was_saved_under(self):
        read = switches_as_read({}, {"1": True, "2": False}, [_listed(1, "snes"), _listed(2, "snes-hacks")])

        assert platform_sync_enabled(read, "snes") is True
        assert platform_sync_enabled(read, "snes-hacks") is False

    def test_while_kept_never_touched_every_listed_platform_is_read_on(self):
        """#2452 D24, D27: an installation that never touched a toggle had every platform on."""
        read = switches_as_read({}, {}, [_listed(1, "snes"), _listed(2, "snes-hacks")])

        assert read == {"snes": True, "snes-hacks": True}

    def test_a_toggle_of_its_own_wins_over_the_one_kept_by_id(self):
        read = switches_as_read({"snes": False}, {"1": True}, [_listed(1, "snes")])

        assert platform_sync_enabled(read, "snes") is False

    def test_two_listed_platforms_sharing_a_folder_read_one_toggle(self):
        read = switches_as_read({}, {"1": True, "2": False}, [_listed(1, "snes"), _listed(2, "snes")])

        assert read == {"snes": True}

    def test_a_listing_naming_no_folder_reads_the_toggles_as_they_stand(self):
        switches = {"snes": True}

        assert switches_as_read(switches, {"1": False}, [_listed(1, None)]) is switches

    def test_it_is_what_the_move_writes(self):
        by_id = {"1": True, "9": True}
        platforms = [_listed(1, "snes"), _listed(2, "snes-hacks"), _listed(3, None)]

        assert switches_as_read({"snes-hacks": True}, by_id, platforms) == _moved(
            by_id, {"snes-hacks": True}, platforms
        )


class TestTheIdsTheMoveDrops:
    """switches_by_folder's dropped ids — those no listed platform with an fs_slug carries (#2452 D21, D26)."""

    def test_an_id_not_listed_or_listed_without_a_folder_is_dropped_in_order(self):
        move = switches_by_folder({"9": False, "1": True, "2": True}, {}, [_listed(1, "snes"), _listed(2, None)])

        assert move is not None
        assert move.dropped == ("2", "9")

    def test_none_dropped_where_every_id_is_listed(self):
        move = switches_by_folder({"1": True}, {}, [_listed(1, "snes")])

        assert move is not None
        assert move.dropped == ()
