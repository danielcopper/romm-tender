"""Tests for services/library/_preview_union.py — the preview's collapse, brought to what the apply's units collapse."""

from __future__ import annotations

from domain.sync_diff import BIND_ROM_ID_KEY
from services.library._preview_union import collapse_counting_platform_rebinds


def _entry(rom_id, key):
    return {"rom_id": rom_id, "sibling_group_key": key}


class _ScriptedCollapse:
    """A collapse that records the ids it was handed and answers each pass from *answers*, in order."""

    def __init__(self, *answers):
        self._answers = list(answers)
        self.passes: list[list[int]] = []

    def __call__(self, entries):
        self.passes.append([entry["rom_id"] for entry in entries])
        return self._answers[len(self.passes) - 1]


class TestCollapseCountingPlatformRebinds:
    def test_a_rebuilt_row_the_second_pass_rebinds_is_replaced_and_no_longer_skipped(self):
        shortcuts = [_entry(20, "g"), _entry(22, "g"), _entry(30, "h")]
        rebind = {**_entry(20, "g"), BIND_ROM_ID_KEY: 22}
        collapse = _ScriptedCollapse([_entry(20, "g"), _entry(30, "h")], [rebind, _entry(30, "h")])

        emitted, skipped = collapse_counting_platform_rebinds(
            shortcuts, collapse, skipped_rom_ids={20, 30}, platform_rom_ids={22}
        )

        assert collapse.passes == [[20, 22, 30], [22, 30]]
        assert emitted == [rebind, _entry(30, "h")]
        assert skipped == frozenset({30})

    def test_a_rebuilt_row_the_second_pass_emits_nothing_for_keeps_its_first_pass_entry_and_stays_skipped(self):
        shortcuts = [_entry(20, "g"), _entry(22, "g")]
        collapse = _ScriptedCollapse([_entry(20, "g")], [])

        emitted, skipped = collapse_counting_platform_rebinds(
            shortcuts, collapse, skipped_rom_ids={20}, platform_rom_ids={22}
        )

        assert collapse.passes == [[20, 22], [22]]
        assert emitted == [_entry(20, "g")]
        assert skipped == frozenset({20})

    def test_with_no_rebuilt_row_in_a_platform_s_group_the_library_is_collapsed_once(self):
        # 20 rides a collection-only group; 22 is a skipped platform's row.
        shortcuts = [_entry(20, "g"), _entry(22, "h")]
        collapse = _ScriptedCollapse([_entry(20, "g"), _entry(22, "h")])

        emitted, skipped = collapse_counting_platform_rebinds(
            shortcuts, collapse, skipped_rom_ids={20, 22}, platform_rom_ids={22}
        )

        assert collapse.passes == [[20, 22]]
        assert emitted == [_entry(20, "g"), _entry(22, "h")]
        assert skipped == frozenset({20, 22})

    def test_a_row_without_a_group_key_is_never_in_a_platform_s_group(self):
        shortcuts = [_entry(20, None), _entry(22, None)]
        collapse = _ScriptedCollapse([_entry(20, None), _entry(22, None)])

        _emitted, skipped = collapse_counting_platform_rebinds(
            shortcuts, collapse, skipped_rom_ids={20}, platform_rom_ids={22}
        )

        assert collapse.passes == [[20, 22]]
        assert skipped == frozenset({20})

    def test_an_empty_library_is_collapsed_once(self):
        collapse = _ScriptedCollapse([])

        assert collapse_counting_platform_rebinds([], collapse, skipped_rom_ids=set(), platform_rom_ids=set()) == (
            [],
            frozenset(),
        )
        assert collapse.passes == [[]]
