"""Tests for domain.sync_diff — pure delta computations for the sync engine."""

from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from typing import Any

import pytest

from domain.sync_diff import (
    BIND_ROM_ID_KEY,
    ClassificationResult,
    ConvergedBinding,
    StaleRow,
    StaleSort,
    classify_roms,
    collapse_sibling_groups,
    compute_collection_diff,
    compute_platform_collection_diff,
    converge_bound_groups,
    gather_unbound_groups,
    group_keys_of,
    groups_to_converge,
    platform_breakdown,
    platform_collection_name,
    rebuilt_rows_filed_as_kept,
    select_stale_removals,
    settle_asked_rows,
    should_include_in_platform_collection,
    sort_stale_rows,
    with_kept_rows_rebound,
)


def _make_sd(
    rom_id,
    name="Game",
    platform_name="N64",
    platform_slug="n64",
    fs_name="game.z64",
    igdb_id=None,
    sgdb_id=None,
    launch_options="",
):
    """Build a shortcut_data dict matching build_shortcuts_data output."""
    return {
        "rom_id": rom_id,
        "name": name,
        "platform_name": platform_name,
        "platform_slug": platform_slug,
        "fs_name": fs_name,
        "igdb_id": igdb_id,
        "sgdb_id": sgdb_id,
        "launch_options": launch_options,
    }


def _reg(
    name="Game",
    platform_name="N64",
    platform_slug="n64",
    fs_name="game.z64",
    app_id=1001,
    applied_launch_options: str | None = "",
):
    """Build a registry entry dict matching do_read_preview_baseline output.

    ``applied_launch_options`` defaults to ``""`` — an uninstalled ROM whose
    empty placeholder was recorded — so a same-identity fetch with the default
    ``_make_sd`` launch_options ("") reads as unchanged (#1383).
    """
    return {
        "app_id": app_id,
        "name": name,
        "platform_name": platform_name,
        "platform_slug": platform_slug,
        "fs_name": fs_name,
        "applied_launch_options": applied_launch_options,
    }


class TestClassifyRoms:
    """classify_roms() — bucketing fetched ROMs against the saved registry."""

    def test_all_new_empty_registry(self):
        sd = [_make_sd(1, "Game A"), _make_sd(2, "Game B")]
        new, changed, unchanged_ids, stale, disabled = classify_roms(sd, {}, {"N64"})
        assert len(new) == 2
        assert changed == []
        assert unchanged_ids == []
        assert stale == []
        assert disabled == 0

    def test_all_unchanged(self):
        registry = {
            "1": _reg(name="Game A", fs_name="gamea.z64", app_id=1001),
            "2": _reg(name="Game B", fs_name="gameb.z64", app_id=1002),
        }
        sd = [
            _make_sd(1, "Game A", fs_name="gamea.z64"),
            _make_sd(2, "Game B", fs_name="gameb.z64"),
        ]
        new, changed, unchanged_ids, stale, _ = classify_roms(sd, registry, {"N64"})
        assert new == []
        assert changed == []
        assert set(unchanged_ids) == {1, 2}
        assert stale == []

    def test_mixed_new_changed_unchanged(self):
        registry = {
            "1": _reg(name="Game A", fs_name="gamea.z64", app_id=1001),
            "2": _reg(name="Old Name", fs_name="gameb.z64", app_id=1002),
        }
        sd = [
            _make_sd(1, "Game A", fs_name="gamea.z64"),  # unchanged
            _make_sd(2, "New Name", fs_name="gameb.z64"),  # changed (name)
            _make_sd(3, "Game C", fs_name="gamec.z64"),  # new
        ]
        new, changed, unchanged_ids, _, _ = classify_roms(sd, registry, {"N64"})
        assert len(new) == 1
        assert new[0]["rom_id"] == 3
        assert len(changed) == 1
        assert changed[0]["rom_id"] == 2
        assert changed[0]["existing_app_id"] == 1002
        assert unchanged_ids == [1]

    def test_stale_detection(self):
        registry = {
            "1": _reg(name="Game A", fs_name="", platform_slug="n64", app_id=1001),
            "99": {"app_id": 1099, "name": "Deleted Game", "platform_name": "N64"},
        }
        sd = [_make_sd(1, "Game A", fs_name="")]
        _, _, _, stale, disabled = classify_roms(sd, registry, {"N64"})
        assert 99 in stale
        assert disabled == 0  # N64 is in fetched_platform_names

    def test_disabled_platform_stale_count(self):
        registry = {
            "1": {"app_id": 1001, "name": "Game A", "platform_name": "SNES"},
        }
        sd: list[dict[str, Any]] = []  # nothing fetched
        _, _, _, stale, disabled = classify_roms(sd, registry, {"N64"})
        assert 1 in stale
        assert disabled == 1  # SNES not in {"N64"}

    def test_name_change_detected(self):
        registry = {
            "1": _reg(name="Old Title", app_id=1001),
        }
        sd = [_make_sd(1, "New Title")]
        new, changed, unchanged_ids, _, _ = classify_roms(sd, registry, {"N64"})
        assert len(changed) == 1
        assert changed[0]["existing_app_id"] == 1001
        assert new == []
        assert unchanged_ids == []

    def test_launch_options_change_detected_with_identical_identity(self):
        # Identity (name/fs_name/platform) is unchanged but the built target
        # launch_options differs from the recorded applied value — a just-installed
        # ROM whose shortcut still carries "" — so the item is "changed" (#1383).
        registry = {"1": _reg(name="Game A", fs_name="gamea.z64", app_id=1001, applied_launch_options="")}
        sd = [_make_sd(1, "Game A", fs_name="gamea.z64", launch_options="flatpak run … /gamea.z64")]
        new, changed, unchanged_ids, _, _ = classify_roms(sd, registry, {"N64"})
        assert new == []
        assert [c["rom_id"] for c in changed] == [1]
        assert changed[0]["existing_app_id"] == 1001
        assert unchanged_ids == []

    def test_matching_launch_options_stays_unchanged(self):
        # Identity AND launch_options both match the recorded applied value — the
        # shortcut is already correct, so the item is genuinely unchanged (skipped).
        cmd = "flatpak run … /gamea.z64"
        registry = {"1": _reg(name="Game A", fs_name="gamea.z64", app_id=1001, applied_launch_options=cmd)}
        sd = [_make_sd(1, "Game A", fs_name="gamea.z64", launch_options=cmd)]
        new, changed, unchanged_ids, _, _ = classify_roms(sd, registry, {"N64"})
        assert new == []
        assert changed == []
        assert unchanged_ids == [1]

    def test_null_applied_launch_options_forces_changed(self):
        # A pre-migration-015 row (or a freshly created row not yet recorded) has
        # applied_launch_options = None (unknown). It never matches a target
        # string, so the item is always "changed" and re-applied once — the "no
        # skip on unknown state / no data invented" contract (#1383). Even when the
        # target launch_options is "" (uninstalled), None != "" holds.
        registry = {"1": _reg(name="Game A", fs_name="gamea.z64", app_id=1001, applied_launch_options=None)}
        sd = [_make_sd(1, "Game A", fs_name="gamea.z64", launch_options="")]
        new, changed, unchanged_ids, _, _ = classify_roms(sd, registry, {"N64"})
        assert new == []
        assert [c["rom_id"] for c in changed] == [1]
        assert unchanged_ids == []

    def test_platform_name_divergence_not_classified_as_changed(self):
        """A derived-display-only divergence never counts as changed (#1292).

        ``platform_name`` is a derived, non-persisted display field. A ROM
        riding an enabled collection whose platform is *disabled* resolves to
        the bare slug ("gba") on the registry side (the enabled-only
        slug→name map has no entry, so it falls back to the slug) but to
        RomM's full display name ("Game Boy Advance") on the fetch side. With
        every persisted field (name/platform_slug/fs_name) equal, that
        divergence must NOT classify as changed — the apply upsert persists
        only platform_slug/name/fs_name, so it could never be healed and
        produced a permanent phantom "1 updated" preview delta.
        """
        registry = {
            "1": _reg(name="Game A", platform_name="gba", platform_slug="gba", fs_name="game.gba", app_id=1001),
        }
        sd = [_make_sd(1, "Game A", platform_name="Game Boy Advance", platform_slug="gba", fs_name="game.gba")]
        new, changed, unchanged_ids, _, _ = classify_roms(sd, registry, {"N64"})
        assert changed == []
        assert new == []
        assert unchanged_ids == [1]

    def test_platform_slug_change_detected(self):
        """A genuine platform change (persisted platform_slug differs) is changed."""
        registry = {
            "1": _reg(name="Game A", platform_name="Game Boy Advance", platform_slug="gba", app_id=1001),
        }
        sd = [_make_sd(1, "Game A", platform_name="Game Boy Color", platform_slug="gbc")]
        _, changed, unchanged_ids, _, _ = classify_roms(sd, registry, {"N64"})
        assert len(changed) == 1
        assert changed[0]["rom_id"] == 1
        assert changed[0]["existing_app_id"] == 1001
        assert unchanged_ids == []

    def test_fs_name_change_detected(self):
        registry = {
            "1": _reg(name="Game A", fs_name="old.z64", app_id=1001),
        }
        sd = [_make_sd(1, "Game A", fs_name="new.z64")]
        _, changed, _, _, _ = classify_roms(sd, registry, {"N64"})
        assert len(changed) == 1
        assert changed[0]["rom_id"] == 1

    def test_igdb_id_change_no_false_positive(self):
        registry = {
            "1": _reg(name="Game A", app_id=1001),
        }
        sd = [_make_sd(1, "Game A", igdb_id=999, sgdb_id=888)]
        new, changed, unchanged_ids, _, _ = classify_roms(sd, registry, {"N64"})
        assert unchanged_ids == [1]
        assert changed == []
        assert new == []

    def test_registry_without_app_id_is_new(self):
        registry = {
            "1": {"name": "Game A", "platform_name": "N64"},
        }
        sd = [_make_sd(1, "Game A")]
        new, changed, _, _, _ = classify_roms(sd, registry, {"N64"})
        assert len(new) == 1
        assert new[0]["rom_id"] == 1
        assert changed == []

    def test_first_sync_empty_registry_all_new(self):
        sd = [_make_sd(i, f"Game {i}") for i in range(1, 6)]
        new, changed, unchanged_ids, stale, disabled = classify_roms(sd, {}, {"N64"})
        assert len(new) == 5
        assert changed == []
        assert unchanged_ids == []
        assert stale == []
        assert disabled == 0

    def test_no_changes(self):
        registry = {
            "1": _reg(name="Game A", app_id=1001),
        }
        sd = [_make_sd(1, "Game A")]
        new, changed, unchanged_ids, stale, _ = classify_roms(sd, registry, {"N64"})
        assert len(new) == 0
        assert len(changed) == 0
        assert len(stale) == 0
        assert len(unchanged_ids) == 1

    def test_all_stale_disabled_platforms(self):
        registry = {
            "1": {"app_id": 1001, "name": "Game A", "platform_name": "GBA"},
            "2": {"app_id": 1002, "name": "Game B", "platform_name": "SNES"},
        }
        sd: list[dict[str, Any]] = []
        _, _, _, stale, disabled = classify_roms(sd, registry, {"N64"})
        assert len(stale) == 2
        assert disabled == 2

    def test_returns_classification_result_namedtuple(self):
        """Result supports both positional unpacking and attribute access."""
        result = classify_roms([_make_sd(1)], {}, {"N64"})
        assert isinstance(result, ClassificationResult)
        # Attribute access
        assert result.new[0]["rom_id"] == 1
        assert result.changed == []
        assert result.unchanged_ids == []
        assert result.stale == []
        assert result.disabled_count == 0
        # Positional unpacking still works
        new, changed, unchanged_ids, stale, disabled = result
        assert new == result.new
        assert changed == result.changed
        assert unchanged_ids == result.unchanged_ids
        assert stale == result.stale
        assert disabled == result.disabled_count

    def test_does_not_mutate_input_shortcuts_data(self):
        """Changed ROMs are returned as fresh dicts; caller's input is untouched."""
        registry = {
            "1": _reg(name="Old Title", app_id=1001),
        }
        sd_item = _make_sd(1, "New Title")
        sd_snapshot = dict(sd_item)
        sd = [sd_item]
        _, changed, _, _, _ = classify_roms(sd, registry, {"N64"})
        # Caller's dict is unchanged — no existing_app_id leaked in
        assert sd_item == sd_snapshot
        assert "existing_app_id" not in sd_item
        # The returned changed entry does carry existing_app_id
        assert changed[0]["existing_app_id"] == 1001


class TestClassifyRomsOnASkippedUnit:
    """classify_roms(skipped_rom_ids=…) — the entries of a unit the apply will skip.

    Apply never rewrites such a unit's shortcuts, so a launch command that differs
    from the recorded one there is no change it would make; only an identity
    difference still counts.
    """

    OLD_CMD = 'flatpak run net.retrodeck.retrodeck "/roms/n64/a.z64"'
    NEW_CMD = 'flatpak run --nosocket=wayland net.retrodeck.retrodeck "/roms/n64/a.z64"'

    def test_a_launch_command_only_difference_is_unchanged(self):
        registry = {"1": _reg(name="Game A", fs_name="a.z64", applied_launch_options=self.OLD_CMD)}
        sd = [_make_sd(1, "Game A", fs_name="a.z64", launch_options=self.NEW_CMD)]

        new, changed, unchanged_ids, _, _ = classify_roms(sd, registry, {"N64"}, skipped_rom_ids=frozenset({1}))

        assert new == []
        assert changed == []
        assert unchanged_ids == [1]

    def test_an_unrecorded_launch_command_is_unchanged(self):
        # NULL is unknown, and outside a skipped unit it forces a re-apply; inside
        # one no apply comes, so it is a launch-command difference like any other.
        registry = {"1": _reg(name="Game A", fs_name="a.z64", applied_launch_options=None)}
        sd = [_make_sd(1, "Game A", fs_name="a.z64", launch_options=self.NEW_CMD)]

        _, changed, unchanged_ids, _, _ = classify_roms(sd, registry, {"N64"}, skipped_rom_ids=frozenset({1}))

        assert changed == []
        assert unchanged_ids == [1]

    @pytest.mark.parametrize(
        ("field", "recorded", "built"),
        [
            ("name", "Old Name", "Game A"),
            ("fs_name", "old.z64", "a.z64"),
            ("platform_slug", "gb", "n64"),
        ],
    )
    def test_an_identity_difference_is_still_changed(self, field, recorded, built):
        reg_fields: dict[str, Any] = {"name": "Game A", "fs_name": "a.z64", "platform_slug": "n64", field: recorded}
        sd_fields: dict[str, Any] = {"name": "Game A", "fs_name": "a.z64", "platform_slug": "n64", field: built}
        registry = {"1": _reg(**reg_fields, applied_launch_options=self.OLD_CMD)}
        sd = [_make_sd(1, **sd_fields, launch_options=self.NEW_CMD)]

        _, changed, unchanged_ids, _, _ = classify_roms(sd, registry, {"N64"}, skipped_rom_ids=frozenset({1}))

        assert [c["rom_id"] for c in changed] == [1]
        assert changed[0]["existing_app_id"] == 1001
        assert unchanged_ids == []

    def test_only_the_skipped_entries_are_spared(self):
        # The same launch-command difference on two games: the one outside the
        # skipped ids is a change the apply will make.
        registry = {
            "1": _reg(name="Game A", fs_name="a.z64", app_id=1001, applied_launch_options=self.OLD_CMD),
            "2": _reg(name="Game B", fs_name="b.z64", app_id=1002, applied_launch_options=self.OLD_CMD),
        }
        sd = [
            _make_sd(1, "Game A", fs_name="a.z64", launch_options=self.NEW_CMD),
            _make_sd(2, "Game B", fs_name="b.z64", launch_options=self.NEW_CMD),
        ]

        _, changed, unchanged_ids, _, _ = classify_roms(sd, registry, {"N64"}, skipped_rom_ids=frozenset({1}))

        assert [c["rom_id"] for c in changed] == [2]
        assert unchanged_ids == [1]


class TestComputeCollectionDiff:
    """compute_collection_diff() — diff enabled collections vs last-synced set."""

    def test_first_sync_with_collections_has_changes(self):
        result = compute_collection_diff({"Alpha"}, [])
        assert result["has_changes"] is True
        assert result["added"] == ["Alpha"]
        assert result["removed"] == []

    def test_empty_current_and_previous_no_changes(self):
        result = compute_collection_diff(set(), [])
        assert result["has_changes"] is False
        assert result["added"] == []
        assert result["removed"] == []

    def test_added_collection_detected(self):
        result = compute_collection_diff({"Alpha", "Beta"}, ["Alpha"])
        assert result["has_changes"] is True
        assert result["added"] == ["Beta"]
        assert result["removed"] == []

    def test_removed_collection_detected(self):
        result = compute_collection_diff({"Alpha"}, ["Alpha", "Beta"])
        assert result["has_changes"] is True
        assert result["added"] == []
        assert result["removed"] == ["Beta"]

    def test_unchanged_collections_still_has_changes_when_current_nonempty(self):
        """has_changes is True even with no add/remove if current is non-empty."""
        result = compute_collection_diff({"Alpha"}, ["Alpha"])
        assert result["has_changes"] is True
        assert result["added"] == []
        assert result["removed"] == []

    def test_added_and_removed_sorted(self):
        result = compute_collection_diff(
            {"Gamma", "Beta", "Delta"},
            ["Sigma", "Kappa"],
        )
        assert result["added"] == ["Beta", "Delta", "Gamma"]
        assert result["removed"] == ["Kappa", "Sigma"]

    def test_same_named_collections_collapse_to_one_present_name(self):
        """A name is present once no matter how many collections carry it (#1503).

        The caller derives the name set from the accumulator, so two same-named
        collections yield a single set member — matching the by-name Steam
        collection they merge into. Diffing that set is unchanged for the run.
        """
        result = compute_collection_diff({"X"}, [])
        assert result["added"] == ["X"]
        assert result["removed"] == []

    def test_case_variants_recorded_as_one_name_show_no_diff(self):
        result = compute_collection_diff({"7 up", "7 Up"}, ["7 up"])
        assert result["added"] == []
        assert result["removed"] == []

    def test_a_change_of_case_alone_is_no_diff(self):
        result = compute_collection_diff({"IGDB Collection"}, ["IGDB collection"])
        assert result["added"] == []
        assert result["removed"] == []

    def test_new_case_variants_count_as_one_added_collection_under_the_sorted_first_spelling(self):
        result = compute_collection_diff({"7 up", "7 Up"}, [])
        assert result["added"] == ["7 Up"]

    def test_names_are_folded_beyond_lower_case(self):
        """``ß`` reaches ``ss`` through upper case, which ``lower`` alone leaves alone."""
        result = compute_collection_diff({"STRASSE"}, ["Straße"])
        assert result["added"] == []
        assert result["removed"] == []

    def test_names_are_folded_by_the_rule_the_frontend_shares(self):
        """A dotless i and ``I`` are one name under the fold both sides compute; ``casefold`` keeps them apart."""
        result = compute_collection_diff({"\u0131x"}, ["Ix"])
        assert result["added"] == []
        assert result["removed"] == []

    def test_a_rename_beyond_case_shows_each_side_under_its_own_spelling(self):
        result = compute_collection_diff({"7 Up Deluxe"}, ["7 up"])
        assert result["added"] == ["7 Up Deluxe"]
        assert result["removed"] == ["7 up"]


class TestShouldIncludeInPlatformCollection:
    """should_include_in_platform_collection() — toggle-aware membership predicate."""

    def test_sc5b_should_include_helper_excludes_collection_only_rom(self):
        """Returns False for collection-only ROM when toggle is OFF."""
        platform_rom_ids = {1, 2}  # ROM 3 is collection-only
        assert should_include_in_platform_collection(1, platform_rom_ids, False) is True
        assert should_include_in_platform_collection(3, platform_rom_ids, False) is False

    def test_sc5b_should_include_helper_includes_all_when_toggle_on(self):
        """Returns True for all ROMs when toggle is ON."""
        platform_rom_ids = {1, 2}
        assert should_include_in_platform_collection(1, platform_rom_ids, True) is True
        assert should_include_in_platform_collection(3, platform_rom_ids, True) is True

    def test_sc5b_should_include_helper_excludes_all_when_no_platforms_enabled(self):
        """Empty set = no platforms enabled -> exclude all (toggle OFF)."""
        assert should_include_in_platform_collection(1, set(), False) is False

    def test_sc5b_should_include_helper_includes_all_when_no_tracking_data(self):
        """None = legacy sync without platform tracking -> include all."""
        assert should_include_in_platform_collection(1, None, False) is True

    def test_sc5b_should_include_helper_includes_all_empty_set_when_toggle_on(self):
        """Empty set + toggle ON -> include all."""
        assert should_include_in_platform_collection(1, set(), True) is True

    def test_should_include_helper_includes_all_when_none_and_toggle_on(self):
        """None + toggle ON -> include all."""
        assert should_include_in_platform_collection(1, None, True) is True


class TestPlatformCollectionName:
    """platform_collection_name() — the platform name a ROM is filed under."""

    def test_a_rom_a_platform_unit_fetched_is_filed_under_the_runs_display_name(self):
        assert platform_collection_name(1, "n64", {1}, {"n64": "Nintendo 64"}, False) == "Nintendo 64"

    def test_a_rom_no_platform_unit_fetched_is_in_no_platform_collection_with_platform_groups_off(self):
        assert platform_collection_name(30, "gba", {1}, {"n64": "Nintendo 64"}, False) is None

    def test_with_platform_groups_on_it_is_filed_under_the_slug_where_no_name_is_held(self):
        assert platform_collection_name(30, "gba", {1}, {"n64": "Nintendo 64"}, True) == "gba"

    def test_no_platform_tracking_files_every_rom(self):
        assert platform_collection_name(30, "n64", None, {"n64": "Nintendo 64"}, False) == "Nintendo 64"


_NAMES = {"gba": "Game Boy Advance", "n64": "Nintendo 64", "psx": "PlayStation"}


class TestComputePlatformCollectionDiff:
    """compute_platform_collection_diff() — diff future platform groups vs last-synced."""

    def test_first_sync_adds_all_platforms(self):
        sd = [
            _make_sd(1, platform_slug="gba"),
            _make_sd(2, platform_slug="n64"),
        ]
        result = compute_platform_collection_diff(sd, {1, 2}, [], False, _NAMES)
        assert result["has_changes"] is True
        assert result["added_count"] == 2
        assert result["removed_count"] == 0

    def test_no_changes_when_platforms_match_last_sync(self):
        sd = [_make_sd(1, platform_slug="gba")]
        result = compute_platform_collection_diff(sd, {1}, ["Game Boy Advance"], False, _NAMES)
        assert result["has_changes"] is False
        assert result["added_count"] == 0
        assert result["removed_count"] == 0

    def test_removed_platform_detected(self):
        sd = [_make_sd(1, platform_slug="gba")]
        result = compute_platform_collection_diff(
            sd,
            {1},
            ["Game Boy Advance", "Nintendo 64"],
            False,
            _NAMES,
        )
        assert result["has_changes"] is True
        assert result["added_count"] == 0
        assert result["removed_count"] == 1

    def test_collection_only_rom_excluded_when_toggle_off(self):
        """ROM not in platform_rom_ids doesn't contribute its platform when toggle is OFF."""
        sd = [
            _make_sd(1, platform_slug="gba"),
            _make_sd(2, platform_slug="psx"),  # collection-only
        ]
        result = compute_platform_collection_diff(sd, {1}, [], False, _NAMES)
        # Only GBA gets added; PSX is filtered out
        assert result["added_count"] == 1

    def test_collection_only_rom_included_when_toggle_on(self):
        """create_platform_groups=True forces every ROM's platform into the diff."""
        sd = [
            _make_sd(1, platform_slug="gba"),
            _make_sd(2, platform_slug="psx"),  # collection-only
        ]
        result = compute_platform_collection_diff(sd, {1}, [], True, _NAMES)
        # Both platforms qualify
        assert result["added_count"] == 2

    def test_none_platform_rom_ids_treats_all_as_qualifying(self):
        """platform_rom_ids=None (legacy sync) includes every ROM regardless of toggle."""
        sd = [_make_sd(1, platform_slug="gba")]
        result = compute_platform_collection_diff(sd, None, [], False, _NAMES)
        assert result["added_count"] == 1

    def test_pure_rebind_platform_not_counted_as_removed(self):
        """A fully-rebinding platform qualifies via bind_rom_id, so it's no change (#1417).

        The rebind entry is keyed to the vanished rom_id (1), absent from the
        fetched ``platform_rom_ids`` ({2}); only its ``bind_rom_id`` target (2)
        is present. Keying membership on the entry's own rom_id would drop the
        platform out of ``future_platforms`` and mis-count it as removed.
        """
        rebind = {**_make_sd(1, platform_slug="n64"), BIND_ROM_ID_KEY: 2}
        result = compute_platform_collection_diff(
            [rebind],
            {2},
            ["Nintendo 64"],
            False,
            _NAMES,
        )
        assert result["has_changes"] is False
        assert result["added_count"] == 0
        assert result["removed_count"] == 0

    def test_rebind_plus_genuinely_removed_platform_counts_only_the_genuine(self):
        """A rebinding platform stays; only a truly-gone platform is removed (#1417)."""
        rebind = {**_make_sd(1, platform_slug="n64"), BIND_ROM_ID_KEY: 2}
        result = compute_platform_collection_diff(
            [rebind],
            {2},
            ["Nintendo 64", "Game Boy Advance"],
            False,
            _NAMES,
        )
        assert result["has_changes"] is True
        assert result["added_count"] == 0
        assert result["removed_count"] == 1

    def test_rebind_plus_added_platform(self):
        """A rebinding platform holds; a freshly-fetched new platform is added (#1417)."""
        rebind = {**_make_sd(1, platform_slug="n64"), BIND_ROM_ID_KEY: 2}
        added = _make_sd(3, platform_slug="psx")
        result = compute_platform_collection_diff(
            [rebind, added],
            {2, 3},
            ["Nintendo 64"],
            False,
            _NAMES,
        )
        assert result["has_changes"] is True
        assert result["added_count"] == 1
        assert result["removed_count"] == 0

    def test_a_change_of_case_alone_is_no_change(self):
        sd = [_make_sd(1, platform_slug="n64")]
        result = compute_platform_collection_diff(sd, {1}, ["NINTENDO 64"], False, _NAMES)
        assert result["has_changes"] is False
        assert result["added_count"] == 0
        assert result["removed_count"] == 0

    def test_new_case_variants_count_as_one_added_platform(self):
        sd = [_make_sd(1, platform_slug="n64"), _make_sd(2, platform_slug="n64-upper")]
        result = compute_platform_collection_diff(
            sd, {1, 2}, [], False, {"n64": "Nintendo 64", "n64-upper": "NINTENDO 64"}
        )
        assert result["added_count"] == 1

    def test_a_rename_beyond_case_still_counts(self):
        sd = [_make_sd(1, platform_slug="n64dd")]
        result = compute_platform_collection_diff(sd, {1}, ["nintendo 64"], False, {"n64dd": "Nintendo 64 DD"})
        assert result["has_changes"] is True
        assert result["added_count"] == 1
        assert result["removed_count"] == 1

    def test_the_platform_name_an_entry_carries_is_not_read(self):
        """A row a collection's skip rebuilt carries its slug as its name; the apply files it by the platform's name."""
        sd = [_make_sd(1, platform_name="n64", platform_slug="n64")]
        result = compute_platform_collection_diff(sd, set(), ["Nintendo 64"], True, {"n64": "Nintendo 64"})
        assert result == {"has_changes": False, "added_count": 0, "removed_count": 0}

    def test_a_platform_without_a_name_is_filed_under_its_slug(self):
        sd = [_make_sd(1, platform_name="Nintendo 64", platform_slug="n64")]
        result = compute_platform_collection_diff(sd, set(), ["n64"], True, {})
        assert result == {"has_changes": False, "added_count": 0, "removed_count": 0}

    def test_a_kept_row_s_platform_is_named_through_the_same_names(self):
        result = compute_platform_collection_diff(
            [], set(), ["Nintendo 64"], False, {"n64": "Nintendo 64"}, kept_platform_slugs={"n64"}
        )
        assert result == {"has_changes": False, "added_count": 0, "removed_count": 0}

    @pytest.mark.parametrize(
        ("platform_groups", "expected"),
        [
            (True, {"has_changes": False, "added_count": 0, "removed_count": 0}),
            (False, {"has_changes": True, "added_count": 0, "removed_count": 1}),
        ],
        ids=["platform-groups-on", "platform-groups-off"],
    )
    def test_a_row_left_bound_is_filed_by_the_setting_alone(self, platform_groups, expected):
        """A stale row the run leaves bound without keeping it is filed as the apply files every bound row (#2490)."""
        left_bound = [StaleRow(7, 6007, "n64", "collection")]
        result = compute_platform_collection_diff(
            [], set(), ["Nintendo 64"], platform_groups, {"n64": "Nintendo 64"}, left_bound_rows=left_bound
        )
        assert result == expected


class TestSelectStaleRemovals:
    """``select_stale_removals`` — drop any stale candidate whose appId this run re-bound (#1036)."""

    def test_excludes_resynced_appid(self):
        """An old colliding rom_id whose appId was re-bound this run is NOT removed.

        rom 1 (old) and rom 2 (new) both resolve to appId 5000; the run bound
        5000 to rom 2, so rom 1 looks stale but its appId is live → excluded."""
        candidate_stale = [(1, 5000)]
        result = select_stale_removals(candidate_stale, {5000})
        assert result == []

    def test_keeps_genuinely_stale_non_resynced_appid(self):
        """A stale ROM whose appId was NOT bound this run is still removed."""
        candidate_stale = [(99, 9900)]
        result = select_stale_removals(candidate_stale, {5000})
        assert result == [(99, 9900)]

    def test_mixed_keeps_only_non_resynced(self):
        """The collision row drops; the genuinely-stale row stays."""
        candidate_stale = [(1, 5000), (99, 9900)]
        result = select_stale_removals(candidate_stale, {5000})
        assert result == [(99, 9900)]

    def test_empty_synced_app_ids_is_passthrough(self):
        """No appId bound this run → every candidate survives unchanged."""
        candidate_stale = [(1, 5000), (99, 9900)]
        result = select_stale_removals(candidate_stale, set())
        assert result == [(1, 5000), (99, 9900)]

    def test_empty_candidates_is_empty(self):
        assert select_stale_removals([], {5000}) == []

    def test_preserves_candidate_order(self):
        candidate_stale = [(3, 3000), (1, 1000), (2, 2000)]
        result = select_stale_removals(candidate_stale, set())
        assert result == [(3, 3000), (1, 1000), (2, 2000)]


def _gsd(
    rom_id,
    *,
    name="Game",
    group_key="g1",
    fs_name="game.z64",
    fs_name_no_ext="game",
    is_main_sibling=False,
    platform_slug="n64",
    launch_options="",
    regions=(),
    revision="",
    tags=(),
):
    """A built shortcut entry as build_shortcuts_data shapes it, with the
    sibling-group fields the collapse + resolver read."""
    return {
        "rom_id": rom_id,
        "name": name,
        "fs_name": fs_name,
        "fs_name_no_ext": fs_name_no_ext,
        "platform_name": "N64",
        "platform_slug": platform_slug,
        "launch_options": launch_options,
        "cover_path": "",
        "sibling_group_key": group_key,
        "is_main_sibling": is_main_sibling,
        "regions": list(regions),
        "revision": revision,
        "tags": list(tags),
        "igdb_id": None,
        "sgdb_id": None,
        "ra_id": None,
    }


def _greg(
    rom_id,
    *,
    app_id,
    name="Game",
    group_key: str | None = "g1",
    fs_name="game.z64",
    platform_slug="n64",
    applied_launch_options="",
):
    """A bound-registry entry as do_read_apply_registry / do_read_preview_baseline shape it.

    ``applied_launch_options`` defaults to ``""`` — the recorded empty placeholder —
    so a same-identity re-fetch with the default ``_gsd`` launch_options ("") reads
    as unchanged (#1383).
    """
    return {
        "app_id": app_id,
        "name": name,
        "fs_name": fs_name,
        "platform_slug": platform_slug,
        "platform_name": "N64",
        "sibling_group_key": group_key,
        "applied_launch_options": applied_launch_options,
    }


def _converge_then_collapse(
    members: list[dict[str, Any]], registry: dict[str, dict[str, Any]], installed_rom_ids: set[int]
) -> tuple[list[dict[str, Any]], list[ConvergedBinding]]:
    """A platform unit's collapse: the convergence over its listing *members*, then the collapse handed its losers."""
    converged = converge_bound_groups(members, registry, installed_rom_ids, platform_slugs={"n64"})
    emitted = collapse_sibling_groups(
        members,
        registry,
        installed_rom_ids,
        complete_group_view=True,
        converged={binding.rom_id for binding in converged},
    )
    return emitted, converged


class TestCollapseSiblingGroups:
    """``collapse_sibling_groups`` — one Steam shortcut per sibling group (ADR-0021)."""

    def test_new_group_emits_single_representative(self):
        # 3 siblings, no binding anywhere; rom 2 is the RomM default → the one rep.
        members = [
            _gsd(1, name="Game (USA)", fs_name_no_ext="game_usa"),
            _gsd(2, name="Game (JP)", fs_name_no_ext="game_jp", is_main_sibling=True),
            _gsd(3, name="Game (EU)", fs_name_no_ext="game_eu"),
        ]
        emitted = collapse_sibling_groups(members, registry={}, installed_rom_ids=set(), complete_group_view=True)
        assert [e["rom_id"] for e in emitted] == [2]

    def test_new_group_representative_and_name_follow_hardened_ranking(self):
        # A New group with a (USA) (Beta) prerelease + a (Japan) retail final: the
        # 1G1R ranking (prerelease demotion before region) picks the Japan retail
        # dump as BOTH the emitted representative (bind target) and the canonical
        # shortcut name — the region-preferred USA dump is a prerelease, so it loses.
        members = [
            _gsd(1, name="Game (USA) (Beta)", fs_name_no_ext="game_usa_beta", regions=["USA"], tags=["Beta"]),
            _gsd(2, name="Game (Japan)", fs_name_no_ext="game_japan", regions=["Japan"]),
        ]
        emitted = collapse_sibling_groups(members, registry={}, installed_rom_ids=set(), complete_group_view=True)
        assert [e["rom_id"] for e in emitted] == [2]
        assert emitted[0]["name"] == "Game (Japan)"

    def test_installed_sibling_wins_representative(self):
        members = [
            _gsd(1, is_main_sibling=True, fs_name_no_ext="a"),
            _gsd(2, fs_name_no_ext="z"),
        ]
        emitted = collapse_sibling_groups(members, registry={}, installed_rom_ids={2}, complete_group_view=True)
        assert [e["rom_id"] for e in emitted] == [2]

    def test_unbound_siblings_not_counted_as_new(self):
        # The #1292-class phantom fix: a group with one bound sibling + two unbound
        # siblings collapses to the bound entry — classify reads it as unchanged,
        # NOT the unbound siblings as perpetual "new".
        members = [
            _gsd(1, name="Game", fs_name="game.z64"),
            _gsd(2, name="Game (JP)", fs_name="game_jp.z64"),
            _gsd(3, name="Game (EU)", fs_name="game_eu.z64"),
        ]
        registry = {"1": _greg(1, app_id=1001, name="Game", fs_name="game.z64")}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        assert [e["rom_id"] for e in emitted] == [1]
        result = classify_roms(emitted, registry, {"N64"})
        assert result.new == []
        assert result.unchanged_ids == [1]
        assert result.stale == []

    def test_multiple_bound_siblings_converge_to_the_one_the_chain_picks(self):
        # Two DIFFERENT-name siblings each already carry a shortcut (a pre-ADR-0021
        # library). Both are still fetched → the chain's pick stays emitted, the
        # other goes on the removal list; no new shortcut for the unbound third.
        members = [
            _gsd(1, name="Game", fs_name="game.z64"),
            _gsd(2, name="Game JP", fs_name="game_jp.z64"),
            _gsd(3, name="Game EU", fs_name="game_eu.z64"),
        ]
        registry = {
            "1": _greg(1, app_id=1001, name="Game", fs_name="game.z64"),
            "2": _greg(2, app_id=1002, name="Game JP", fs_name="game_jp.z64"),
        }
        emitted, converged = _converge_then_collapse(members, registry, set())
        assert [e["rom_id"] for e in emitted] == [1]
        assert converged == [ConvergedBinding(2, 1002, 1, 1001)]

    def test_whole_group_vanished_is_stale(self):
        # The bound row's group has NO fetched member at all → collapse emits
        # nothing for it, and classify flags it stale.
        members = [_gsd(9, name="Other", group_key="g2")]
        registry = {"1": _greg(1, app_id=1001, name="Gone", group_key="g1")}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        assert 1 not in {e["rom_id"] for e in emitted}
        result = classify_roms(emitted, registry, {"N64"})
        assert result.stale == [1]

    def test_a_vanished_bound_sibling_keeps_its_binding_beside_live_siblings_under_other_names(self):
        # rom 1 is the group's bound sibling but vanished from the server; roms 2/3
        # survive under other names. Nothing moves the shortcut onto them: the
        # collapse emits nothing for the group, and the stale removal keeps the
        # binding, the appId and the launch command where they are.
        members = [
            _gsd(
                2,
                name="Game (JP)",
                fs_name="game_jp.z64",
                fs_name_no_ext="game_jp",
                is_main_sibling=True,
                launch_options="run /jp.z64",
            ),
            _gsd(3, name="Game (EU)", fs_name="game_eu.z64", fs_name_no_ext="game_eu"),
        ]
        registry = {"1": _greg(1, app_id=1001, name="Game (USA)", fs_name="game_usa.z64")}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        assert emitted == []

    def test_an_installed_vanished_version_moves_onto_the_same_dump_under_a_new_id(self):
        # D14: the same dump under a reissued id takes the binding, installed or not.
        members = [
            _gsd(2, name="Game (USA)", fs_name="game_usa.z64", fs_name_no_ext="game_usa", launch_options=""),
            _gsd(3, name="Game (JP)", fs_name="game_jp.z64", fs_name_no_ext="game_jp", is_main_sibling=True),
        ]
        registry = {"1": _greg(1, app_id=1001, name="Game (USA)", fs_name="game_usa.z64")}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids={1}, complete_group_view=True)
        assert [(e["rom_id"], e[BIND_ROM_ID_KEY]) for e in emitted] == [(1, 2)]

    def test_a_vanished_bound_sibling_beside_a_surviving_one_converges_onto_the_surviving_one(self):
        members = [
            _gsd(2, name="Game (JP)", fs_name="game_jp.z64", fs_name_no_ext="game_jp"),
            _gsd(3, name="Game (USA)", fs_name="game_usa.z64", fs_name_no_ext="game_usa"),
        ]
        registry = {
            "1": _greg(1, app_id=1001, name="Game (USA)", fs_name="game_usa.z64"),
            "2": _greg(2, app_id=1002, name="Game (JP)", fs_name="game_jp.z64"),
        }
        emitted, converged = _converge_then_collapse(members, registry, set())
        assert [(e["rom_id"], e.get(BIND_ROM_ID_KEY)) for e in emitted] == [(2, None)]
        assert converged == [ConvergedBinding(1, 1001, 2, 1002)]

    def test_server_reimport_fresh_rom_id_rides_rebind_lane_not_a_duplicate(self):
        # The #1366 clean re-import: a server switch / re-import reissues the
        # rom_id of an unchanged game (same content, same sibling_group_key), and
        # the old rom_id is still bound to its assigned Steam appId. The group's
        # only fetched member is the FRESH rom_id, so collapse takes the REBIND
        # lane — one entry keyed to the vanished old rom_id (the frontend reuses
        # its existing shortcut) carrying bind_rom_id → the fresh rom_id. There is
        # NO new-lane entry, so no duplicate AddShortcut and no second tile.
        members = [_gsd(2, name="Game (USA)", fs_name="game.z64", launch_options="run /game.z64")]
        registry = {"1": _greg(1, app_id=1001, name="Game (USA)", fs_name="game.z64")}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        assert len(emitted) == 1
        entry = emitted[0]
        assert entry["rom_id"] == 1  # keyed to the vanished old rom_id → shortcut reused
        assert entry[BIND_ROM_ID_KEY] == 2  # binding moves onto the fresh rom_id
        # The fresh rom_id is never emitted as its own NEW shortcut.
        assert 2 not in {e["rom_id"] for e in emitted}

        # classify reads the rebind as a re-touch, never stale or new — the old
        # rom_id stays emitted, so it is not torn down.
        result = classify_roms(emitted, registry, {"N64"})
        assert result.new == []
        assert result.stale == []
        assert [e["rom_id"] for e in result.changed] == [1]

        # The reused appId is committed this run (the frontend reuses rom 1 →
        # 1001). The #1036 guard: even if the old rom_id surfaced as a stale
        # candidate carrying that still-live appId, select_stale_removals never
        # removes an appId bound this run — so a re-import cannot wipe the shortcut
        # it just re-bound.
        assert select_stale_removals([(1, 1001)], {1001}) == []

    def test_of_two_vanished_bound_siblings_the_lowest_rom_id_keeps_its_binding(self):
        # A group with TWO bound siblings, both vanished, and a survivor under
        # another name: the lower rom_id keeps its shortcut, which does not move;
        # the other is removed.
        members = [_gsd(5, name="Game (EU)", fs_name_no_ext="game_eu")]
        registry = {
            "1": _greg(1, app_id=1001, name="Game (USA)"),
            "2": _greg(2, app_id=1002, name="Game (JP)"),
        }
        emitted, converged = _converge_then_collapse(members, registry, set())
        assert emitted == []
        assert converged == [ConvergedBinding(2, 1002, 1, 1001)]

    def test_of_two_vanished_bound_siblings_of_one_dump_the_first_takes_it(self):
        members = [_gsd(5, fs_name_no_ext="game")]
        registry = {"1": _greg(1, app_id=1001), "2": _greg(2, app_id=1002)}
        emitted, converged = _converge_then_collapse(members, registry, set())
        assert [(e["rom_id"], e[BIND_ROM_ID_KEY]) for e in emitted] == [(1, 5)]
        assert converged == [ConvergedBinding(2, 1002, 1, 1001)]

    def test_legacy_null_key_bound_row_grandfathered_via_fetched_key(self):
        # A bound row whose stored sibling_group_key is still NULL (pre-capture) is
        # grouped by its FETCHED key, so it is grandfathered (kept), never churned
        # into a delete + recreate.
        members = [_gsd(1, name="Game", group_key="igdb:42:7")]
        registry = {"1": _greg(1, app_id=1001, name="Game", group_key=None)}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        assert [e["rom_id"] for e in emitted] == [1]
        result = classify_roms(emitted, registry, {"N64"})
        assert result.unchanged_ids == [1]

    def test_solo_unmatched_group_degrades_to_per_rom(self):
        # An unmatched ROM is a solo group — collapse emits it exactly as before.
        members = [_gsd(7, name="Homebrew", group_key="romm:7:1")]
        emitted = collapse_sibling_groups(members, registry={}, installed_rom_ids=set(), complete_group_view=True)
        assert [e["rom_id"] for e in emitted] == [7]


class TestConvergeBoundGroups:
    """``converge_bound_groups`` — a group holding more than one bound shortcut keeps one (#2645 D1, D3)."""

    @staticmethod
    def _kept(members, registry, installed: Iterable[int] = (), platform_slugs: AbstractSet[str] = frozenset({"n64"})):
        converged = converge_bound_groups(members, registry, set(installed), platform_slugs=platform_slugs)
        return [(binding.rom_id, binding.kept_rom_id) for binding in converged]

    @pytest.mark.parametrize(
        ("installed", "kept"), [((), 1), ((2,), 2), ((1,), 1), ((1, 2), 2)], ids=["none", "two", "one", "both"]
    )
    def test_an_installed_listed_version_keeps_the_shortcut_else_the_chain_s_pick(self, installed, kept):
        # 1 is RomM's default; 2 is the USA dump, which the region ranking puts first
        # among installed versions.
        members = [
            _gsd(1, fs_name_no_ext="game_jp", is_main_sibling=True, regions=["Japan"]),
            _gsd(2, fs_name_no_ext="game_usa", regions=["USA"]),
        ]
        registry = {"1": _greg(1, app_id=1001), "2": _greg(2, app_id=1002)}
        removed = 2 if kept == 1 else 1
        assert self._kept(members, registry, installed) == [(removed, kept)]

    def test_an_installed_unlisted_version_beats_a_listed_one_not_installed(self):
        members = [_gsd(2, fs_name_no_ext="game_jp")]
        registry = {"1": _greg(1, app_id=1001), "2": _greg(2, app_id=1002)}
        assert self._kept(members, registry, {1}) == [(2, 1)]

    def test_an_installed_listed_version_beats_an_installed_unlisted_one(self):
        members = [_gsd(2, fs_name_no_ext="game_jp")]
        registry = {"1": _greg(1, app_id=1001), "2": _greg(2, app_id=1002)}
        assert self._kept(members, registry, {1, 2}) == [(1, 2)]

    def test_a_listed_version_with_a_file_beats_romm_s_default_without_one(self):
        members = [
            _no_file(_gsd(1, fs_name_no_ext="game_jp", is_main_sibling=True)),
            _gsd(2, fs_name_no_ext="game_usa"),
        ]
        registry = {"1": _greg(1, app_id=1001), "2": _greg(2, app_id=1002)}
        assert self._kept(members, registry) == [(1, 2)]

    def test_a_listed_version_without_a_file_beats_an_unlisted_one(self):
        members = [_no_file(_gsd(2, fs_name_no_ext="game_jp"))]
        registry = {"1": _greg(1, app_id=1001), "2": _greg(2, app_id=1002)}
        assert self._kept(members, registry) == [(1, 2)]

    @pytest.mark.parametrize(("installed", "kept"), [((), 1), ((3,), 3)], ids=["none", "the-highest"])
    def test_a_game_romm_no_longer_lists_at_all_keeps_its_installed_version_else_the_lowest_rom_id(
        self, installed, kept
    ):
        members = [_gsd(9, group_key="other")]
        registry = {str(rom_id): _greg(rom_id, app_id=1000 + rom_id) for rom_id in (1, 2, 3)}
        removed = [rom_id for rom_id in (1, 2, 3) if rom_id != kept]
        assert self._kept(members, registry, installed) == [(rom_id, kept) for rom_id in removed]

    def test_the_binding_carries_both_app_ids(self):
        members = [_gsd(1), _gsd(2)]
        registry = {"1": _greg(1, app_id=1001), "2": _greg(2, app_id=1002)}
        assert converge_bound_groups(members, registry, set(), platform_slugs={"n64"}) == [
            ConvergedBinding(rom_id=2, app_id=1002, kept_rom_id=1, kept_app_id=1001)
        ]

    def test_a_group_with_one_binding_converges_nothing(self):
        members = [_gsd(1), _gsd(2)]
        assert self._kept(members, {"1": _greg(1, app_id=1001)}) == []

    def test_rows_without_a_group_key_are_never_one_group(self):
        registry = {"1": _greg(1, app_id=1001, group_key=None), "2": _greg(2, app_id=1002, group_key=None)}
        assert self._kept([], registry) == []

    def test_a_listed_legacy_row_without_a_stored_key_joins_its_listed_group(self):
        members = [_gsd(1, group_key="g1"), _gsd(2, group_key="g1")]
        registry = {"1": _greg(1, app_id=1001, group_key=None), "2": _greg(2, app_id=1002)}
        assert self._kept(members, registry) == [(2, 1)]

    def test_a_platform_outside_the_view_converges_nothing(self):
        registry = {
            "1": _greg(1, app_id=1001, platform_slug="snes"),
            "2": _greg(2, app_id=1002, platform_slug="snes"),
        }
        assert self._kept([], registry, platform_slugs={"n64"}) == []

    def test_each_group_converges_on_its_own(self):
        members = [_gsd(1, group_key="a"), _gsd(2, group_key="a"), _gsd(3, group_key="b"), _gsd(4, group_key="b")]
        registry = {
            "1": _greg(1, app_id=1001, group_key="a"),
            "2": _greg(2, app_id=1002, group_key="a"),
            "3": _greg(3, app_id=1003, group_key="b"),
            "4": _greg(4, app_id=1004, group_key="b"),
        }
        assert sorted(self._kept(members, registry)) == [(2, 1), (4, 3)]


class TestAGroupWithARunningGameIsLeftOut:
    """``converge_bound_groups(running_rom_ids=...)`` — a group one of whose shortcuts runs waits (#2645 D5)."""

    @staticmethod
    def _converged(running):
        members = [_gsd(1, group_key="a"), _gsd(2, group_key="a"), _gsd(3, group_key="b"), _gsd(4, group_key="b")]
        registry = {
            "1": _greg(1, app_id=1001, group_key="a"),
            "2": _greg(2, app_id=1002, group_key="a"),
            "3": _greg(3, app_id=1003, group_key="b"),
            "4": _greg(4, app_id=1004, group_key="b"),
        }
        converged = converge_bound_groups(members, registry, set(), platform_slugs={"n64"}, running_rom_ids=running)
        return sorted((binding.rom_id, binding.kept_rom_id) for binding in converged)

    @pytest.mark.parametrize("running", [{1}, {2}], ids=["the-kept-one", "the-other"])
    def test_a_running_shortcut_leaves_its_group_as_it_is_and_the_others_converge(self, running):
        assert self._converged(running) == [(4, 3)]

    def test_a_running_rom_outside_every_group_changes_nothing(self):
        assert self._converged({9}) == [(2, 1), (4, 3)]


class TestCollapseHandedTheConvergedRows:
    """``collapse_sibling_groups(converged=...)``: a converged row emits nothing and still holds its shortcut."""

    def test_a_converged_listed_row_is_not_emitted(self):
        members = [_gsd(1), _gsd(2)]
        registry = {"1": _greg(1, app_id=1001), "2": _greg(2, app_id=1002)}
        emitted = collapse_sibling_groups(members, registry, set(), complete_group_view=True, converged={2})
        assert [e["rom_id"] for e in emitted] == [1]

    def test_the_kept_shortcut_never_moves_onto_a_converged_row_in_the_same_run(self):
        # 1 vanished but is installed, so it keeps the shortcut; 2 is the same dump
        # and loses its own. Moving 1's shortcut onto 2 now would have the finalize,
        # which unbinds 2, unbind the shortcut just moved onto it.
        members = [_gsd(2, fs_name="game.z64", fs_name_no_ext="game")]
        registry = {"1": _greg(1, app_id=1001, fs_name="game.z64"), "2": _greg(2, app_id=1002, fs_name="game.z64")}
        emitted, converged = _converge_then_collapse(members, registry, {1})
        assert converged == [ConvergedBinding(2, 1002, 1, 1001)]
        assert emitted == []

    def test_a_group_whose_bound_rows_all_converged_mints_no_shortcut(self):
        members = [_gsd(1), _gsd(2)]
        registry = {"1": _greg(1, app_id=1001), "2": _greg(2, app_id=1002)}
        emitted = collapse_sibling_groups(members, registry, set(), complete_group_view=True, converged={1, 2})
        assert emitted == []


class TestTheKeptRowFollowsARebind:
    """``with_kept_rows_rebound`` — the row bound to the kept shortcut once the run commits."""

    def test_a_kept_row_the_collapse_rebinds_names_the_row_it_moves_onto(self):
        converged = [ConvergedBinding(2, 1002, 1, 1001)]
        emitted = [{**_gsd(5), "rom_id": 1, BIND_ROM_ID_KEY: 5}]
        assert with_kept_rows_rebound(converged, emitted) == [ConvergedBinding(2, 1002, 5, 1001)]

    def test_a_kept_row_the_collapse_leaves_stays(self):
        converged = [ConvergedBinding(2, 1002, 1, 1001)]
        assert with_kept_rows_rebound(converged, [_gsd(1)]) == converged


class TestGroupsToConverge:
    """``groups_to_converge`` — the keys more than one bound row carries; the skip gate asks it too."""

    def test_a_key_two_bound_rows_carry_converges(self):
        assert groups_to_converge(["a", "a", "b"]) == frozenset({"a"})

    def test_no_key_carried_twice_converges_nothing(self):
        assert groups_to_converge(["a", "b"]) == frozenset()

    def test_rows_without_a_key_are_never_a_group(self):
        assert groups_to_converge([None, None]) == frozenset()


class TestCollapseSiblingGroupsCanonicalNaming:
    """Mint entries carry the region-canonical name; bound lanes keep the persisted name (ADR-0021 §2/§3)."""

    def test_mint_binds_region_representative_and_names_it_canonically(self):
        # The Pokémon fix: no binding anywhere, USA + Japan dumps. Region priority
        # binds USA AND names the shortcut after USA — not the alphabetically-first
        # Japanese dump.
        members = [
            _gsd(1, name="ポケットモンスターファイアレッド", fs_name_no_ext="game_japan", regions=["Japan"]),
            _gsd(2, name="Pokemon FireRed", fs_name_no_ext="game_usa", regions=["USA"]),
        ]
        emitted = collapse_sibling_groups(members, registry={}, installed_rom_ids=set(), complete_group_view=True)
        assert len(emitted) == 1
        assert emitted[0]["rom_id"] == 2
        assert emitted[0]["name"] == "Pokemon FireRed"

    def test_mint_name_decoupled_from_bound_target_when_default_forces_it(self):
        # RomM default = the Japanese dump, so the BIND target is Japan, but the
        # canonical NAME still follows region priority (USA) — name decoupled from
        # the bound version (ADR-0021 "name can lag the active version").
        members = [
            _gsd(1, name="Japan Default", fs_name_no_ext="game_japan", regions=["Japan"], is_main_sibling=True),
            _gsd(2, name="USA Name", fs_name_no_ext="game_usa", regions=["USA"]),
        ]
        emitted = collapse_sibling_groups(members, registry={}, installed_rom_ids=set(), complete_group_view=True)
        assert len(emitted) == 1
        assert emitted[0]["rom_id"] == 1  # bind the RomM default (Japan)
        assert emitted[0]["name"] == "USA Name"  # but name it canonically (USA)

    def test_mint_does_not_mutate_the_source_member(self):
        # The emitted entry is a copy: the original member dict (also staged in
        # pending_all_roms for the per-sibling identity upsert) keeps its own name.
        japan = _gsd(1, name="Japan Name", fs_name_no_ext="game_japan", regions=["Japan"], is_main_sibling=True)
        members = [japan, _gsd(2, name="USA Name", fs_name_no_ext="game_usa", regions=["USA"])]
        collapse_sibling_groups(members, registry={}, installed_rom_ids=set(), complete_group_view=True)
        assert japan["name"] == "Japan Name"

    def test_mint_respects_preferred_region_override(self):
        members = [
            _gsd(1, name="Euro Name", fs_name_no_ext="game_eu", regions=["Europe"]),
            _gsd(2, name="German Name", fs_name_no_ext="game_de", regions=["Germany"]),
        ]
        emitted = collapse_sibling_groups(
            members, registry={}, installed_rom_ids=set(), complete_group_view=True, preferred_region="Germany"
        )
        assert emitted[0]["rom_id"] == 2
        assert emitted[0]["name"] == "German Name"

    def test_grandfathered_bound_sibling_keeps_persisted_name_no_rename(self):
        # A bound group must never be renamed by canonical naming — the emitted
        # grandfathered entry carries the fetched member's own name, matching the
        # persisted registry name, so classify reads it as unchanged (no churn).
        members = [
            _gsd(1, name="Japan Name", fs_name_no_ext="game_japan", regions=["Japan"]),
            _gsd(2, name="USA Name", fs_name_no_ext="game_usa", regions=["USA"]),
        ]
        registry = {"1": _greg(1, app_id=1001, name="Japan Name", fs_name="game.z64")}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        assert [e["rom_id"] for e in emitted] == [1]
        assert emitted[0]["name"] == "Japan Name"
        result = classify_roms(emitted, registry, {"N64"})
        assert result.unchanged_ids == [1]
        assert result.changed == []

    def test_a_same_dump_rebind_keeps_the_persisted_name_not_the_canonical_one(self):
        # A rebinding group is already bound → sticky: the rebind entry carries the
        # vanished sibling's PERSISTED name, never the region-canonical name, so the
        # live shortcut is not renamed.
        members = [
            _gsd(2, name="USA Name", fs_name_no_ext="game_usa", regions=["USA"]),
            _gsd(3, name="Euro Name", fs_name_no_ext="game_eu", regions=["Europe"]),
        ]
        registry = {"1": _greg(1, app_id=1001, name="Original Bound Name", fs_name="game_usa.z64")}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        assert len(emitted) == 1
        assert emitted[0]["rom_id"] == 1
        assert emitted[0]["name"] == "Original Bound Name"
        assert emitted[0][BIND_ROM_ID_KEY] == 2


class TestCollapseSiblingGroupsPartialView:
    """``collapse_sibling_groups`` under a PARTIAL group view (collection units, #1296).

    A collection unit spans platforms and fetches only its own members, so the
    whole-registry read surfaces bound siblings the unit never fetched. Absence
    from that partial fetch is NOT absence from the server, so the collapse must
    never rebind (which would move a live installed game's shortcut onto an
    uninstalled sibling) — it only grandfathers.
    """

    def test_bound_sibling_absent_from_partial_fetch_is_grandfathered_not_rebound(self):
        # The worked #1296 failure in miniature: rom 1 is bound + installed but on
        # a platform this collection unit never fetched; the collection fetches
        # only the UNBOUND sibling rom 2. A partial view must leave the binding
        # untouched — emit nothing, never a rebind entry onto the uninstalled rom 2.
        members = [_gsd(2, name="Game (USA)", fs_name="game_usa.z64", fs_name_no_ext="game_usa")]
        registry = {"1": _greg(1, app_id=1001, name="Game (USA)", fs_name="game_usa.z64")}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids={1}, complete_group_view=False)
        assert emitted == []
        # And the contrast: the SAME inputs under a complete view DO rebind onto
        # the same dump — the only thing separating "grandfather" from "rebind"
        # is view-completeness.
        rebound = collapse_sibling_groups(members, registry, installed_rom_ids={1}, complete_group_view=True)
        assert len(rebound) == 1
        assert rebound[0]["rom_id"] == 1
        assert rebound[0][BIND_ROM_ID_KEY] == 2

    def test_partial_view_group_with_no_binding_anywhere_mints_representative(self):
        # A group with NO binding in the whole registry IS a genuinely new game —
        # a collection-only unit still mints its single representative among the
        # fetched members (RomM default rom 2 here).
        members = [
            _gsd(1, name="Game (USA)", fs_name_no_ext="game_usa"),
            _gsd(2, name="Game (JP)", fs_name_no_ext="game_jp", is_main_sibling=True),
        ]
        emitted = collapse_sibling_groups(members, registry={}, installed_rom_ids=set(), complete_group_view=False)
        assert [e["rom_id"] for e in emitted] == [2]

    def test_partial_view_fetched_bound_sibling_still_emits_as_update(self):
        # A bound sibling that IS in the partial fetch still emits (as an update
        # entry) so its identity/launch refresh; the unbound fetched sibling does
        # not mint a second shortcut. No rebind either — the group is grandfathered.
        members = [
            _gsd(1, name="Game", fs_name="game.z64"),
            _gsd(2, name="Game (JP)", fs_name="game_jp.z64"),
        ]
        registry = {"1": _greg(1, app_id=1001, name="Game", fs_name="game.z64")}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=False)
        assert [e["rom_id"] for e in emitted] == [1]
        assert all(BIND_ROM_ID_KEY not in e for e in emitted)


class TestCollapseSiblingGroupsViewPerGroup:
    """``complete_group_view`` as the set of groups held whole: the preview's union (#2536 D2)."""

    @staticmethod
    def _vanished_and_fileless():
        """g1's bound 1 left RomM, its dump is 2; g2's bound 3 has no file, its dump is 4."""
        members = [
            _gsd(2, group_key="g1", fs_name_no_ext="a"),
            _no_file(_gsd(3, group_key="g2", fs_name_no_ext="b")),
            _gsd(4, group_key="g2", fs_name_no_ext="b"),
        ]
        registry = {
            "1": _greg(1, app_id=1001, group_key="g1", fs_name="a.z64"),
            "3": _greg(3, app_id=1003, group_key="g2", fs_name="b.z64"),
        }
        return members, registry

    @pytest.mark.parametrize(
        ("whole", "expected"),
        [
            ({"g1", "g2"}, [(1, 2), (3, 4)]),
            ({"g1"}, [(1, 2), (3, None)]),
            ({"g2"}, [(3, 4)]),
            (set(), [(3, None)]),
        ],
        ids=["both", "g1", "g2", "none"],
    )
    def test_only_a_group_held_whole_rebinds(self, whole, expected):
        members, registry = self._vanished_and_fileless()
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=whole)
        assert sorted((e["rom_id"], e.get(BIND_ROM_ID_KEY)) for e in emitted) == expected

    @pytest.mark.parametrize(("whole", "flag"), [({"g1", "g2"}, True), (set(), False)], ids=["every", "none"])
    def test_a_set_naming_every_group_or_none_answers_as_the_flag_does(self, whole, flag):
        members, registry = self._vanished_and_fileless()
        by_set = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=whole)
        by_flag = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=flag)
        assert by_set == by_flag


class TestGatherUnboundGroups:
    """A group only collections deliver is gathered into the first delivery holding a member (#2543 D4)."""

    @staticmethod
    def _rom(rom_id, group_key="g1"):
        return {"id": rom_id, "sibling_group_key": group_key}

    @staticmethod
    def _ids(deliveries):
        return [[rom["id"] for rom in delivery] for delivery in deliveries]

    @pytest.mark.parametrize(("first", "second"), [(23, 22), (22, 23)], ids=["jp-first", "eu-first"])
    def test_an_unbound_group_moves_wholly_into_the_first_delivery_holding_a_member(self, first, second):
        gathered = gather_unbound_groups([[self._rom(first)], [self._rom(second)]], registry={})
        assert self._ids(gathered) == [[first, second], []]

    def test_a_delivery_before_the_group_s_first_member_keeps_only_its_own(self):
        deliveries = [[self._rom(1, "g0")], [self._rom(23)], [self._rom(22)]]
        assert self._ids(gather_unbound_groups(deliveries, registry={})) == [[1], [23, 22], []]

    def test_a_moved_member_follows_the_delivery_s_own_roms(self):
        deliveries = [[self._rom(23), self._rom(5, "g5")], [self._rom(6, "g6"), self._rom(22)]]
        assert self._ids(gather_unbound_groups(deliveries, registry={})) == [[23, 5, 22], [6]]

    def test_a_group_whose_bound_row_is_delivered_stays_where_it_was_delivered(self):
        deliveries = [[self._rom(23)], [self._rom(22)]]
        registry = {"22": _greg(22, app_id=6022)}
        assert self._ids(gather_unbound_groups(deliveries, registry)) == [[23], [22]]

    def test_a_group_whose_bound_row_is_not_delivered_stays_where_it_was_delivered(self):
        deliveries = [[self._rom(23)], [self._rom(22)]]
        registry = {"21": _greg(21, app_id=6021)}
        assert self._ids(gather_unbound_groups(deliveries, registry)) == [[23], [22]]

    def test_a_delivered_bound_row_counts_under_its_delivered_key_not_its_stored_one(self):
        # The stored key still names another group; the delivered key is the one
        # the collapse groups the row by, so g1 is bound and g2 is not.
        deliveries = [[self._rom(23), self._rom(30, "g2")], [self._rom(22), self._rom(31, "g2")]]
        registry = {"22": _greg(22, app_id=6022, group_key="g2")}
        assert self._ids(gather_unbound_groups(deliveries, registry)) == [[23, 30, 31], [22]]

    def test_a_row_without_a_shortcut_binds_nothing(self):
        deliveries = [[self._rom(23)], [self._rom(22)]]
        registry = {"22": _greg(22, app_id=None)}
        assert self._ids(gather_unbound_groups(deliveries, registry)) == [[23, 22], []]

    def test_no_deliveries_gather_nothing(self):
        assert gather_unbound_groups([], registry={}) == []


class TestPlatformBreakdown:
    """platform_breakdown — the preview's library-wide counts split per platform."""

    def test_groups_counts_per_platform_sorted_by_display_name(self):
        new = [_make_sd(1, platform_slug="snes"), _make_sd(2, platform_slug="n64"), _make_sd(3, platform_slug="snes")]
        changed = [_make_sd(4, platform_slug="n64")]
        registry = {"9": _reg(platform_slug="gba", platform_name="Game Boy Advance")}
        platforms = {"n64": "Nintendo 64", "snes": "Super Nintendo", "gba": "Game Boy Advance"}
        rows = platform_breakdown(new, changed, [9], registry, platforms, platforms)

        assert [row["name"] for row in rows] == ["Game Boy Advance", "Nintendo 64", "Super Nintendo"]
        assert rows[0] == {
            "slug": "gba",
            "name": "Game Boy Advance",
            "synced": True,
            "new_count": 0,
            "changed_count": 0,
            "remove_count": 1,
        }
        assert rows[1]["new_count"] == 1
        assert rows[1]["changed_count"] == 1
        assert rows[2]["new_count"] == 2

    def test_synced_platform_name_comes_from_the_run_map(self):
        # The registry's own name is stale here; the run's map is what the row states.
        registry = {"1": _reg(platform_slug="n64", platform_name="N64")}
        platforms = {"n64": "Nintendo 64"}
        rows = platform_breakdown([_make_sd(5, platform_slug="n64")], [], [], registry, platforms, platforms)
        assert rows == [
            {
                "slug": "n64",
                "name": "Nintendo 64",
                "synced": True,
                "new_count": 1,
                "changed_count": 0,
                "remove_count": 0,
            }
        ]

    def test_switched_off_platform_is_unsynced_with_removals_and_its_listed_name(self):
        # One of the two ways out of the run: the toggle went off, so the
        # platform is not fetched and its rows read stale. RomM still lists it,
        # so the names map holds its name, which the row states rather than the
        # registry projection's slug.
        registry = {"7": _reg(platform_slug="gg", platform_name="gg")}
        rows = platform_breakdown([], [], [7], registry, {"n64"}, {"n64": "Nintendo 64", "gg": "Game Gear"})
        assert rows == [
            {
                "slug": "gg",
                "name": "Game Gear",
                "synced": False,
                "new_count": 0,
                "changed_count": 0,
                "remove_count": 1,
            }
        ]

    def test_collection_reached_platform_is_unsynced_with_changes_and_its_listed_name_not_the_entrys(self):
        # The other way out of the run: an enabled COLLECTION reaches a ROM on a
        # platform whose toggle is off, so the entry arrives as new/changed with
        # RomM's display name on it — here a custom name set in RomM. The row
        # states the listed name the names map holds, as the apply files it.
        new = [_make_sd(1, platform_slug="gg", platform_name="GG Custom")]
        changed = [_make_sd(2, platform_slug="gg", platform_name="GG Custom")]
        registry = {"2": _reg(platform_slug="gg", platform_name="gg")}
        rows = platform_breakdown(new, changed, [], registry, {"n64"}, {"n64": "Nintendo 64", "gg": "Game Gear"})
        assert rows == [
            {
                "slug": "gg",
                "name": "Game Gear",
                "synced": False,
                "new_count": 1,
                "changed_count": 1,
                "remove_count": 0,
            }
        ]

    def test_run_map_name_beats_the_entrys_own(self):
        # A synced platform takes the run's display name even where the entry
        # carries RomM's — the map is the run's own vocabulary.
        rows = platform_breakdown(
            [_make_sd(1, platform_slug="n64", platform_name="Nintendo 64 (RomM)")],
            [],
            [],
            {},
            {"n64": "Nintendo 64"},
            {"n64": "Nintendo 64"},
        )
        assert rows[0]["name"] == "Nintendo 64"

    def test_neither_an_entrys_nor_the_registrys_name_is_read(self):
        # An entry carries the slug where a skipped collection rebuilt it and
        # RomM's display name where a collection fetched it; the registry
        # carries what an earlier read resolved. None of them is the name the
        # apply files the platform under, so with no name in the map the row
        # reads the slug, in whichever order the entries arrive.
        placeholder = _make_sd(1, platform_slug="gg", platform_name="gg")
        real = _make_sd(2, platform_slug="gg", platform_name="Game Gear")
        registry = {"5": _reg(platform_slug="gg", platform_name="Game Gear")}
        assert platform_breakdown([placeholder, real], [], [], registry, {}, {})[0]["name"] == "gg"
        assert platform_breakdown([real, placeholder], [], [], registry, {}, {})[0]["name"] == "gg"
        assert platform_breakdown([placeholder], [real], [], registry, {}, {})[0]["name"] == "gg"

    def test_platform_with_no_counts_is_absent(self):
        # In the run and in the registry, but nothing to do — no row.
        registry = {"1": _reg(platform_slug="n64", platform_name="Nintendo 64")}
        platforms = {"n64": "Nintendo 64", "snes": "Super Nintendo"}
        rows = platform_breakdown([], [], [], registry, platforms, platforms)
        assert rows == []

    def test_sorting_is_case_insensitive(self):
        rows = platform_breakdown(
            [_make_sd(1, platform_slug="a"), _make_sd(2, platform_slug="b"), _make_sd(3, platform_slug="c")],
            [],
            [],
            {},
            {"a", "b", "c"},
            {"a": "zeta", "b": "Alpha", "c": "Beta"},
        )
        assert [row["name"] for row in rows] == ["Alpha", "Beta", "zeta"]

    def test_empty_inputs_yield_no_rows(self):
        assert platform_breakdown([], [], [], {}, {}, {}) == []


#: Slug → display name for the conservation tripwire's run map. ``gg`` is
#: deliberately absent from it — a platform outside the run, so the tripwire
#: covers the unsynced case and the three counts one such row can carry.
_TRIPWIRE_PLATFORMS = {"n64": "Nintendo 64", "snes": "Super Nintendo", "gba": "Game Boy Advance"}

#: Every listed platform's name per slug, as the preview's names map holds it —
#: including the one slug the run map does not hold. A built entry carries it too.
_TRIPWIRE_ENTRY_NAMES = {**_TRIPWIRE_PLATFORMS, "gg": "Game Gear"}


def _tripwire_mix():
    """Build a mixed classification across four platforms.

    Returns ``(new, changed, stale, registry)``: every third ROM is new, every
    third changed, the rest stale, cycling over the four platform slugs so the
    three buckets and the platforms interleave rather than lining up.
    """
    slugs = ["n64", "snes", "gba", "gg"]
    new: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    stale: list[int] = []
    registry: dict[str, Any] = {}
    for rom_id in range(1, 41):
        slug = slugs[rom_id % len(slugs)]
        registry[str(rom_id)] = _reg(platform_slug=slug, platform_name=_TRIPWIRE_PLATFORMS.get(slug, slug))
        entry = _make_sd(rom_id, platform_slug=slug, platform_name=_TRIPWIRE_ENTRY_NAMES[slug])
        if rom_id % 3 == 0:
            new.append(entry)
        elif rom_id % 3 == 1:
            changed.append(entry)
        else:
            stale.append(rom_id)
    return new, changed, stale, registry


def _column_sums(rows: list[dict[str, Any]]) -> tuple[int, int, int]:
    return (
        sum(row["new_count"] for row in rows),
        sum(row["changed_count"] for row in rows),
        sum(row["remove_count"] for row in rows),
    )


class TestPlatformBreakdownConservesTheClassification:
    """The breakdown re-buckets the classification — it must not lose or double an item."""

    def test_columns_sum_to_the_classification_and_each_slug_appears_once(self):
        new, changed, stale, registry = _tripwire_mix()
        rows = platform_breakdown(new, changed, stale, registry, _TRIPWIRE_PLATFORMS, _TRIPWIRE_ENTRY_NAMES)

        assert _column_sums(rows) == (len(new), len(changed), len(stale))
        slugs = [row["slug"] for row in rows]
        assert sorted(slugs) == sorted(set(slugs))
        assert set(slugs) == {"n64", "snes", "gba", "gg"}
        # The one slug outside the run map: flagged unsynced, named from the
        # names map, and carrying all three counts at once — the causes of leaving
        # the run compose, so removals and changes sit on one row.
        outside = next(row for row in rows if row["slug"] == "gg")
        assert outside["synced"] is False
        assert outside["name"] == "Game Gear"
        assert outside["new_count"]
        assert outside["changed_count"]
        assert outside["remove_count"]

    def test_an_extra_stale_id_moves_the_sums(self):
        """Mutation probe: the check above can fail, so its passing means something."""
        new, changed, stale, registry = _tripwire_mix()
        before = _column_sums(
            platform_breakdown(new, changed, stale, registry, _TRIPWIRE_PLATFORMS, _TRIPWIRE_ENTRY_NAMES)
        )

        extra_id = 99
        registry[str(extra_id)] = _reg(platform_slug="n64", platform_name="Nintendo 64")
        stale.append(extra_id)
        after = _column_sums(
            platform_breakdown(new, changed, stale, registry, _TRIPWIRE_PLATFORMS, _TRIPWIRE_ENTRY_NAMES)
        )

        assert after == (before[0], before[1], before[2] + 1)
        assert after[2] == len(stale)


def _no_file(entry: dict[str, Any]) -> dict[str, Any]:
    """*entry* as built for a ROM RomM holds no file for."""
    return {**entry, "has_file_on_disk": False}


class TestCollapseMembersWithoutAFile:
    """Members RomM holds no file for (#2196): the representative and the rebind off one."""

    def test_a_new_group_mints_the_present_member_where_the_ranking_ties(self):
        members = [_no_file(_gsd(1, fs_name_no_ext="Game (USA)")), _gsd(2, fs_name_no_ext="Game (USA)")]
        emitted = collapse_sibling_groups(members, registry={}, installed_rom_ids=set(), complete_group_view=True)
        assert [e["rom_id"] for e in emitted] == [2]

    def test_a_physical_rom_is_a_member_without_a_file(self):
        physical = {**_gsd(1, fs_name_no_ext="Game (USA)"), "has_file_on_disk": False, "is_physical": True}
        members = [physical, _gsd(2, fs_name_no_ext="Game (USA)")]
        emitted = collapse_sibling_groups(members, registry={}, installed_rom_ids=set(), complete_group_view=True)
        assert [e["rom_id"] for e in emitted] == [2]

    def test_a_group_with_no_file_anywhere_still_mints_its_shortcut(self):
        members = [_no_file(_gsd(1, fs_name_no_ext="b")), _no_file(_gsd(2, fs_name_no_ext="a"))]
        emitted = collapse_sibling_groups(members, registry={}, installed_rom_ids=set(), complete_group_view=True)
        assert [e["rom_id"] for e in emitted] == [2]

    def test_a_bound_member_without_a_file_is_rebound_onto_the_same_dump_with_a_file(self):
        # The Saturn case: a rescan turned "Game (USA).zip" into a folder "Game (USA)".
        members = [
            _no_file(_gsd(1, fs_name="Game (USA).zip", fs_name_no_ext="Game (USA)")),
            _gsd(2, fs_name="Game (USA)", fs_name_no_ext="Game (USA)"),
        ]
        registry = {"1": _greg(1, app_id=1001, fs_name="Game (USA).zip")}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        assert len(emitted) == 1
        assert emitted[0]["rom_id"] == 1  # keyed by the bound row, so its shortcut and appId are reused
        assert emitted[0][BIND_ROM_ID_KEY] == 2
        assert emitted[0]["fs_name"] == "Game (USA).zip"  # the sticky identity stays the bound row's

    @pytest.mark.parametrize(
        "other_stem",
        ["Game (USA) (v1.1)", "Game (USA) (De)", "Game (USA) (Hack)", "Game (Europe)", "game (usa)"],
    )
    def test_a_sibling_whose_name_differs_takes_no_rebind(self, other_stem):
        members = [_no_file(_gsd(1, fs_name_no_ext="Game (USA)")), _gsd(2, fs_name_no_ext=other_stem)]
        registry = {"1": _greg(1, app_id=1001)}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        assert [(e["rom_id"], BIND_ROM_ID_KEY in e) for e in emitted] == [(1, False)]

    def test_a_bound_group_with_no_file_anywhere_stays_bound_as_it_is(self):
        # D4: the shortcut stays on its member; no rebind, and no second shortcut.
        members = [
            _no_file(_gsd(1, fs_name="Game (USA).zip", fs_name_no_ext="Game (USA)")),
            _no_file(_gsd(2, fs_name="Game (USA)", fs_name_no_ext="Game (USA)", is_main_sibling=True)),
        ]
        registry = {"1": _greg(1, app_id=1001, fs_name="Game (USA).zip")}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        assert [(e["rom_id"], BIND_ROM_ID_KEY in e) for e in emitted] == [(1, False)]

    def test_an_empty_stem_matches_nothing(self):
        members = [_no_file(_gsd(1, fs_name_no_ext="")), _gsd(2, fs_name_no_ext="")]
        registry = {"1": _greg(1, app_id=1001)}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        assert [(e["rom_id"], BIND_ROM_ID_KEY in e) for e in emitted] == [(1, False)]

    def test_a_same_dump_holding_its_own_shortcut_takes_no_rebind(self):
        members = [_no_file(_gsd(1, fs_name_no_ext="Game (USA)")), _gsd(2, fs_name_no_ext="Game (USA)")]
        registry = {"1": _greg(1, app_id=1001), "2": _greg(2, app_id=1002)}
        emitted, converged = _converge_then_collapse(members, registry, set())
        assert [(e["rom_id"], BIND_ROM_ID_KEY in e) for e in emitted] == [(2, False)]
        assert converged == [ConvergedBinding(1, 1001, 2, 1002)]

    def test_of_two_same_dumps_with_a_file_the_chain_picks(self):
        members = [
            _no_file(_gsd(1, fs_name_no_ext="Game (USA)")),
            _gsd(2, fs_name_no_ext="Game (USA)"),
            _gsd(3, fs_name_no_ext="Game (USA)", is_main_sibling=True),
        ]
        registry = {"1": _greg(1, app_id=1001)}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        assert [(e["rom_id"], e[BIND_ROM_ID_KEY]) for e in emitted] == [(1, 3)]

    def test_two_bound_members_without_a_file_never_rebind_onto_one_dump(self):
        members = [
            _no_file(_gsd(1, fs_name_no_ext="Game (USA)")),
            _no_file(_gsd(2, fs_name_no_ext="Game (USA)")),
            _gsd(3, fs_name_no_ext="Game (USA)"),
        ]
        registry = {"1": _greg(1, app_id=1001), "2": _greg(2, app_id=1002)}
        emitted, converged = _converge_then_collapse(members, registry, set())
        assert [(e["rom_id"], e.get(BIND_ROM_ID_KEY)) for e in emitted] == [(1, 3)]
        assert converged == [ConvergedBinding(2, 1002, 1, 1001)]

    def test_an_installed_bound_member_without_a_file_stays_bound(self):
        members = [_no_file(_gsd(1, fs_name_no_ext="Game (USA)")), _gsd(2, fs_name_no_ext="Game (USA)")]
        registry = {"1": _greg(1, app_id=1001)}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids={1}, complete_group_view=True)
        assert [(e["rom_id"], BIND_ROM_ID_KEY in e) for e in emitted] == [(1, False)]

    def test_a_partial_view_never_rebinds_off_a_member_without_a_file(self):
        members = [_no_file(_gsd(1, fs_name_no_ext="Game (USA)")), _gsd(2, fs_name_no_ext="Game (USA)")]
        registry = {"1": _greg(1, app_id=1001)}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=False)
        assert [(e["rom_id"], BIND_ROM_ID_KEY in e) for e in emitted] == [(1, False)]


class TestClassifyCountsARebindAsChanged:
    """Every entry carrying ``bind_rom_id`` is changed, so the preview offers Apply for it alone."""

    def test_a_rebind_off_a_member_without_a_file_is_changed(self):
        members = [_no_file(_gsd(1, fs_name_no_ext="Game (USA)")), _gsd(2, fs_name_no_ext="Game (USA)")]
        registry = {"1": _greg(1, app_id=1001)}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        result = classify_roms(emitted, registry, {"N64"})
        assert [e["rom_id"] for e in result.changed] == [1]
        assert result.unchanged_ids == []

    def test_a_rebind_keyed_by_a_skipped_unit_s_row_is_no_change(self):
        # The apply moves no binding for a skipped unit, so its rebuilt stem
        # (the file name without its extension) can decide nothing.
        members = [
            _no_file(_gsd(1, fs_name="Game (USA).zip", fs_name_no_ext="Game (USA)")),
            _gsd(2, fs_name="Game (USA)", fs_name_no_ext="Game (USA)"),
        ]
        registry = {"1": _greg(1, app_id=1001, fs_name="Game (USA).zip")}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        assert emitted[0][BIND_ROM_ID_KEY] == 2
        result = classify_roms(emitted, registry, {"N64"}, skipped_rom_ids=frozenset({1}))
        assert result.changed == []
        assert result.unchanged_ids == [1]

    def test_the_rebind_after_a_re_import_is_changed(self):
        # The bound rom 1 vanished from RomM; the group's rom 2 survives.
        members = [_gsd(2, fs_name_no_ext="game")]
        registry = {"1": _greg(1, app_id=1001)}
        emitted = collapse_sibling_groups(members, registry, installed_rom_ids=set(), complete_group_view=True)
        assert emitted[0][BIND_ROM_ID_KEY] == 2
        result = classify_roms(emitted, registry, {"N64"})
        assert [e["rom_id"] for e in result.changed] == [1]
        assert result.unchanged_ids == []


class TestSortStaleRows:
    """``sort_stale_rows`` / ``settle_asked_rows`` — which stale shortcut goes (D4, D11)."""

    def test_each_row_lands_by_what_the_run_knows(self):
        read = StaleRow(1, 1001, "n64", "platform")
        via_collection = StaleRow(2, 1002, "gba", "collection")
        via_platform_turned_off = StaleRow(3, 1003, "gba", "platform")
        via_platform_gone_from_romm = StaleRow(4, 1004, "psx", "platform")
        unrecorded_listed = StaleRow(5, 1005, "gba")
        unrecorded_unlisted = StaleRow(6, 1006, "gba")
        unrecorded_platform_gone = StaleRow(7, 1007, "psx")

        sort = sort_stale_rows(
            [
                read,
                via_collection,
                via_platform_turned_off,
                via_platform_gone_from_romm,
                unrecorded_listed,
                unrecorded_unlisted,
                unrecorded_platform_gone,
            ],
            read_platform_slugs={"n64"},
            listed_platform_slugs={"n64", "gba"},
            collection_listed_rom_ids={5},
        )

        assert sort == StaleSort(
            remove=[via_platform_turned_off, unrecorded_unlisted],
            keep=[read, via_platform_gone_from_romm, unrecorded_platform_gone],
            ask=[via_collection, unrecorded_listed],
        )

    def test_a_collection_route_is_asked_even_on_a_platform_romm_no_longer_lists(self):
        row = StaleRow(1, 1001, "psx", "collection")

        sort = sort_stale_rows(
            [row], read_platform_slugs=set(), listed_platform_slugs=set(), collection_listed_rom_ids=()
        )

        assert sort.ask == [row]

    def test_an_unknown_listing_reads_as_every_platform_listed(self):
        row = StaleRow(1, 1001, "psx", "platform")

        sort = sort_stale_rows(
            [row], read_platform_slugs=set(), listed_platform_slugs=None, collection_listed_rom_ids=()
        )

        assert sort.remove == [row]

    def test_nothing_stale_sorts_into_nothing(self):
        assert sort_stale_rows(
            [], read_platform_slugs=set(), listed_platform_slugs=set(), collection_listed_rom_ids=set()
        ) == StaleSort([], [], [])

    def test_a_row_whose_game_a_unit_delivered_is_never_removed_and_asked_only_where_another_would_be(self):
        """A version switch bound a member no unit delivers; the run delivered another member of its group."""
        listed_turned_off = StaleRow(1, 1001, "gba", None, "igdb:1:1")
        via_collection = StaleRow(2, 1002, "gba", "collection", "igdb:2:1")
        via_platform_turned_off = StaleRow(3, 1003, "gba", "platform", "igdb:3:1")
        via_platform_gone_from_romm = StaleRow(4, 1004, "psx", "platform", "igdb:4:1")
        unrecorded_listed = StaleRow(5, 1005, "gba", None, "igdb:5:1")

        sort = sort_stale_rows(
            [
                listed_turned_off,
                via_collection,
                via_platform_turned_off,
                via_platform_gone_from_romm,
                unrecorded_listed,
            ],
            read_platform_slugs=set(),
            listed_platform_slugs={"gba"},
            collection_listed_rom_ids={5},
            synced_group_keys={"igdb:1:1", "igdb:2:1", "igdb:3:1", "igdb:4:1", "igdb:5:1"},
        )

        assert sort == StaleSort([], [], [], (via_collection, unrecorded_listed))
        assert sort.gone_from_romm_marks({}, set()) == {}

    def test_a_row_asked_only_for_its_mark_is_marked_and_cleared_like_any_asked_row(self):
        gone, present, unknown = (StaleRow(rid, 1000 + rid, "gba", "collection", "igdb:1:1") for rid in (1, 2, 3))
        sort = sort_stale_rows(
            [gone, present, unknown],
            read_platform_slugs=set(),
            listed_platform_slugs={"gba"},
            collection_listed_rom_ids=(),
            synced_group_keys={"igdb:1:1"},
        )

        assert sort.gone_from_romm_marks({1: "gone", 2: "present", 3: "unknown"}, set()) == {1: "not_found", 2: None}

    def test_a_row_on_a_platform_read_whole_is_kept_whether_or_not_its_game_was_delivered(self):
        row = StaleRow(1, 1001, "n64", "platform", "igdb:1:1")

        sort = sort_stale_rows(
            [row],
            read_platform_slugs={"n64"},
            listed_platform_slugs={"n64"},
            collection_listed_rom_ids=(),
            synced_group_keys={"igdb:1:1"},
        )

        assert sort.keep == [row]

    @pytest.mark.parametrize("key", [None, "igdb:2:1"], ids=["no group key", "another game"])
    def test_a_row_whose_own_game_no_unit_delivered_is_judged_alone(self, key):
        row = StaleRow(1, 1001, "gba", None, key)

        sort = sort_stale_rows(
            [row],
            read_platform_slugs=set(),
            listed_platform_slugs={"gba"},
            collection_listed_rom_ids=(),
            synced_group_keys={"igdb:1:1"},
        )

        assert sort.remove == [row]

    def test_only_a_rom_romm_returned_is_removed(self):
        gone, present, unknown, unasked = (StaleRow(rid, 1000 + rid, "gba") for rid in (1, 2, 3, 4))

        remove, keep = settle_asked_rows([gone, present, unknown, unasked], {1: "gone", 2: "present", 3: "unknown"})

        assert (remove, keep) == ([present], [gone, unknown, unasked])


class TestGroupKeysOf:
    def test_the_keys_held_for_the_ids_asked_and_nothing_for_an_id_without_one(self):
        keys = {1: "igdb:1:1", 2: "igdb:1:1", 3: "igdb:3:1", 4: "igdb:4:1"}

        assert group_keys_of({1, 2, 3, 5}, keys) == frozenset({"igdb:1:1", "igdb:3:1"})


class TestRebuiltRowsFiledAsKept:
    """``rebuilt_rows_filed_as_kept`` — the rebuilt rows filed in their platform's collection as kept rows are."""

    def test_a_row_its_platform_read_whole_did_not_return_is_filed(self):
        assert rebuilt_rows_filed_as_kept({20: "gba"}, {"gba"}, {22, 23}) == [20]

    def test_a_row_on_a_platform_the_run_did_not_read_is_not_filed(self):
        assert rebuilt_rows_filed_as_kept({20: "gba"}, {"n64"}, set()) == []

    def test_a_row_its_platform_returned_is_not_filed_again(self):
        assert rebuilt_rows_filed_as_kept({20: "gba"}, {"gba"}, {20}) == []


class TestGoneFromRommMarks:
    """``StaleSort.gone_from_romm_marks`` — what an apply records about RomM still serving a stale row (#2431)."""

    def test_a_404_marks_a_found_rom_clears_and_an_unknown_answer_writes_nothing(self):
        gone, present, unknown, unanswered = (StaleRow(rid, 1000 + rid, "gba", "collection") for rid in (1, 2, 3, 4))
        sort = StaleSort(remove=[], keep=[], ask=[gone, present, unknown, unanswered])

        marks = sort.gone_from_romm_marks({1: "gone", 2: "present", 3: "unknown"}, set())

        assert marks == {1: "not_found", 2: None}

    def test_a_row_kept_on_a_platform_romm_no_longer_lists_is_marked_delisted(self):
        read_whole = StaleRow(1, 1001, "n64", "platform")
        delisted = StaleRow(2, 1002, "psx", "platform")
        sort = sort_stale_rows(
            [read_whole, delisted],
            read_platform_slugs={"n64"},
            listed_platform_slugs={"n64"},
            collection_listed_rom_ids=(),
        )

        assert sort.gone_from_romm_marks({}, {"n64"}) == {2: "delisted"}

    def test_a_row_the_run_removes_unasked_writes_nothing(self):
        turned_off = StaleRow(1, 1001, "gba", "platform")
        sort = sort_stale_rows(
            [turned_off], read_platform_slugs=set(), listed_platform_slugs={"gba"}, collection_listed_rom_ids=()
        )

        assert sort.gone_from_romm_marks({}, set()) == {}
