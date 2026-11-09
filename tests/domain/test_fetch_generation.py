"""Tests for the skip's countable-row kernel (``domain/fetch_generation.py``)."""

from __future__ import annotations

import pytest

from domain.fetch_generation import (
    backfill_needed,
    count_rows_for_skip,
    current_generation_ids,
    gone_from_romm_ids,
    hidden_without_a_listed_version,
    prune_candidate_ids,
)
from domain.platform_sync_state import PlatformSyncState
from domain.rom import Rom


def _row(rom_id: int, fetch_id: str | None) -> Rom:
    rom = Rom.synced(
        rom_id=rom_id,
        platform_slug="dc",
        platform_fs_slug="dc",
        name=f"rom-{rom_id}",
        fs_name=f"rom-{rom_id}.gdi",
        shortcut_app_id=None,
        synced_at="2026-07-20T06:27:12",
    )
    if fetch_id is not None:
        rom.record_fetch_generation(fetch_id)
    return rom


class TestCountRowsForSkip:
    def test_counts_only_rows_of_the_named_generation(self):
        # The live #1504 shape: 4375/4376 superseded, 25135/25136 current.
        rows = [
            _row(4375, "run-old"),
            _row(4376, "run-old"),
            _row(25135, "run-new"),
            _row(25136, "run-new"),
        ]
        assert count_rows_for_skip(rows, "run-new") == 2

    def test_superseded_rows_stop_blocking_the_server_count_match(self):
        rows = [_row(4375, "run-old"), _row(25135, "run-new"), _row(25136, "run-new")]
        server_rom_count = 2
        assert count_rows_for_skip(rows, "run-new") == server_rom_count

    def test_counts_bound_and_unbound_rows_alike(self):
        # Group-aware sync persists every sibling (ADR-0021); only the generation
        # decides, never the binding.
        bound = _row(25135, "run-new")
        bound.bind_shortcut(4185303886)
        assert count_rows_for_skip([bound, _row(25136, "run-new")], "run-new") == 2

    def test_row_with_no_generation_never_matches(self):
        assert count_rows_for_skip([_row(4375, None), _row(25135, "run-new")], "run-new") == 1

    def test_unknown_stamp_generation_counts_every_row(self):
        # A stamp written before the generation contract cannot say what its fetch
        # saw, so the pre-#1504 behavior stands until the next fetch re-stamps.
        rows = [_row(4375, None), _row(25135, "run-new")]
        assert count_rows_for_skip(rows, None) == 2

    def test_empty_stamp_generation_counts_every_row(self):
        assert count_rows_for_skip([_row(4375, None)], "") == 1

    def test_no_rows_counts_zero(self):
        assert count_rows_for_skip([], "run-new") == 0
        assert count_rows_for_skip([], None) == 0

    def test_a_platform_with_no_superseded_rows_counts_all_of_them(self):
        rows = [_row(25135, "run-new"), _row(25136, "run-new")]
        assert count_rows_for_skip(rows, "run-new") == len(rows)


class TestPruneCandidateIds:
    def test_mismatch_and_null_are_candidates(self):
        stamp = PlatformSyncState.stamp(platform_folder="dc", at="now", rom_count=1, fetch_id="new")
        assert prune_candidate_ids([_row(1, "new"), _row(2, "old"), _row(3, None)], stamp) == {2, 3}

    def test_missing_legacy_and_empty_completion_yield_none(self):
        rows = [_row(1, "old")]
        assert prune_candidate_ids(rows, None) == set()
        assert (
            prune_candidate_ids(
                rows, PlatformSyncState.stamp(platform_folder="dc", at="now", rom_count=1, fetch_id=None)
            )
            == set()
        )
        assert (
            prune_candidate_ids(
                rows, PlatformSyncState.stamp(platform_folder="dc", at="now", rom_count=0, fetch_id="new")
            )
            == set()
        )


class TestGoneFromRommIds:
    def test_the_stamp_s_candidates_and_every_marked_row(self):
        stamp = PlatformSyncState.stamp(platform_folder="dc", at="now", rom_count=1, fetch_id="new")
        returned_but_marked = _row(1, "new")
        returned_but_marked.mark_gone_from_romm("delisted")

        assert gone_from_romm_ids([returned_but_marked, _row(2, "old"), _row(3, "new")], stamp) == {1, 2}

    @pytest.mark.parametrize("evidence", ["not_found", "delisted"])
    def test_a_marked_row_is_named_without_a_stamp(self, evidence):
        marked = _row(1, None)
        marked.mark_gone_from_romm(evidence)

        assert gone_from_romm_ids([marked, _row(2, None)], None) == {1}


def _keyed_row(rom_id: int, fetch_id: str | None, group_key: str | None) -> Rom:
    return Rom(
        rom_id=rom_id,
        platform_slug="dc",
        platform_fs_slug="dc",
        name=f"rom-{rom_id}",
        fs_name=f"rom-{rom_id}.gdi",
        shortcut_app_id=None,
        last_synced_at="2026-07-20T06:27:12",
        sibling_group_key=group_key,
        last_fetch_id=fetch_id,
    )


class TestBackfillNeeded:
    def test_null_key_in_the_stamped_generation_demands_a_backfill(self):
        rows = [_keyed_row(25135, "run-new", "group-a"), _keyed_row(25136, "run-new", None)]
        assert backfill_needed(rows, "run-new") is True

    def test_null_key_on_a_dropped_row_may_not_hold_the_skip_off(self):
        """#1504: no fetch can ever fill a key the server no longer returns."""
        rows = [_keyed_row(4375, "run-old", None), _keyed_row(25135, "run-new", "group-a")]
        assert backfill_needed(rows, "run-new") is False

    def test_every_key_present_needs_no_backfill(self):
        rows = [_keyed_row(25135, "run-new", "group-a"), _keyed_row(25136, "run-new", "group-a")]
        assert backfill_needed(rows, "run-new") is False

    def test_a_stamp_without_a_generation_counts_every_null_key(self):
        """The legacy path predates the contract and errs towards fetching."""
        rows = [_keyed_row(4375, "run-old", None)]
        assert backfill_needed(rows, None) is True
        assert backfill_needed(rows, "") is True

    def test_no_rows_never_demands_a_backfill(self):
        assert backfill_needed([], "run-new") is False
        assert backfill_needed([], None) is False


class TestCurrentGenerationIds:
    def test_returns_only_rows_carrying_the_stamped_generation(self):
        rows = [_row(4375, "run-old"), _row(25135, "run-new"), _row(25136, "run-new")]
        stamp = PlatformSyncState.stamp(platform_folder="dc", at="now", rom_count=2, fetch_id="run-new")
        assert current_generation_ids(rows, stamp) == {25135, 25136}

    def test_is_the_exact_complement_of_the_candidate_set(self):
        rows = [_row(4375, "run-old"), _row(25135, "run-new")]
        stamp = PlatformSyncState.stamp(platform_folder="dc", at="now", rom_count=1, fetch_id="run-new")
        assert current_generation_ids(rows, stamp) | prune_candidate_ids(rows, stamp) == {4375, 25135}
        assert current_generation_ids(rows, stamp) & prune_candidate_ids(rows, stamp) == set()

    @pytest.mark.parametrize(
        "stamp",
        [
            None,
            PlatformSyncState.stamp(platform_folder="dc", at="now", rom_count=1, fetch_id=""),
            PlatformSyncState.stamp(platform_folder="dc", at="now", rom_count=0, fetch_id="run-new"),
        ],
    )
    def test_an_unusable_stamp_establishes_nothing(self, stamp):
        """No stamp, no generation, or an empty fetch cannot vouch for any row."""
        assert current_generation_ids([_row(25135, "run-new")], stamp) == set()

    def test_no_rows_yields_nothing(self):
        stamp = PlatformSyncState.stamp(platform_folder="dc", at="now", rom_count=1, fetch_id="run-new")
        assert current_generation_ids([], stamp) == set()


def _game_row(rom_id: int, *, key: str | None, fetch_id: str, app_id: int | None = None) -> Rom:
    """A row of the game *key*, bound to *app_id*, that the fetch *fetch_id* last returned."""
    from dataclasses import replace

    return replace(_row(rom_id, fetch_id), sibling_group_key=key, shortcut_app_id=app_id)


class TestHiddenWithoutAListedVersion:
    """Which bound rows a platform skip leaves to the stale removal (#2502 D2)."""

    def test_a_bound_hidden_row_whose_game_the_fetch_did_not_return_is_named(self):
        rows = [_game_row(1, key="g1", fetch_id="old", app_id=11), _game_row(2, key="g2", fetch_id="new", app_id=12)]
        assert hidden_without_a_listed_version(rows, {1}, "new") == {1}

    def test_a_visible_version_the_fetch_returned_keeps_it(self):
        rows = [_game_row(1, key="g", fetch_id="old", app_id=11), _game_row(2, key="g", fetch_id="new")]
        assert hidden_without_a_listed_version(rows, {1}, "new") == set()

    def test_a_version_of_an_older_fetch_or_one_also_hidden_keeps_nothing(self):
        rows = [
            _game_row(1, key="g", fetch_id="old", app_id=11),
            _game_row(2, key="g", fetch_id="old"),
            _game_row(3, key="g", fetch_id="new"),
        ]
        assert hidden_without_a_listed_version(rows, {1, 3}, "new") == {1}

    def test_a_row_without_a_group_key_stands_alone(self):
        rows = [_game_row(1, key=None, fetch_id="old", app_id=11), _game_row(2, key=None, fetch_id="new")]
        assert hidden_without_a_listed_version(rows, {1}, "new") == {1}

    def test_an_unbound_hidden_row_is_not_named(self):
        assert hidden_without_a_listed_version([_game_row(1, key="g", fetch_id="old")], {1}, "new") == set()
