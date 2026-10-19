"""Unit tests for ``domain/platform_skip.py`` — the platform incremental skip's rule."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from domain.platform_skip import (
    SKIP_REFERENCE_MARGIN,
    parse_romm_timestamp,
    probe_verdict,
    restamp_due,
    rom_id_checksum,
    skip_reference,
    stamp_can_match,
)
from domain.platform_sync_state import PlatformSyncState

_STARTED = "2026-10-19T12:00:00+00:00"
# Ten minutes before the fetch started: the stamp's reference.
_REFERENCE = "2026-10-19T11:50:00+00:00"


def _stamp(*, ids=(1, 2, 3), started: str | None = _STARTED, checksum: str | None = "auto") -> PlatformSyncState:
    return PlatformSyncState.stamp(
        platform_slug="n64",
        at="2026-10-19T12:30:00+00:00",
        rom_count=len(ids),
        fetch_id="run-1",
        fetch_started_at=started,
        rom_id_checksum=rom_id_checksum(ids) if checksum == "auto" else checksum,
    )


def _answer(newest: str | None, ids: list[int]) -> dict[str, Any]:
    items = [] if newest is None else [{"id": ids[0], "updated_at": newest}]
    return {"items": items, "total": len(ids), "rom_id_index": ids}


class TestRomIdChecksum:
    def test_order_does_not_matter(self):
        assert rom_id_checksum([3, 1, 2]) == rom_id_checksum([1, 2, 3])

    def test_a_repeated_id_does_not_matter(self):
        assert rom_id_checksum([1, 2, 2, 3]) == rom_id_checksum([1, 2, 3])

    def test_is_the_sha256_of_the_comma_joined_sorted_ids(self):
        import hashlib

        assert rom_id_checksum([10, 2]) == hashlib.sha256(b"2,10").hexdigest()

    def test_ids_sort_as_numbers_not_as_text(self):
        assert rom_id_checksum([10, 9]) == rom_id_checksum([9, 10])
        assert rom_id_checksum([10, 9]) != rom_id_checksum([1, 9, 10])

    def test_one_more_id_differs(self):
        assert rom_id_checksum([1, 2, 3, 4]) != rom_id_checksum([1, 2, 3])

    def test_one_swapped_id_differs(self):
        assert rom_id_checksum([1, 2, 4]) != rom_id_checksum([1, 2, 3])

    def test_no_ids_has_a_checksum_of_its_own(self):
        assert rom_id_checksum([]) == rom_id_checksum(())
        assert rom_id_checksum([]) != rom_id_checksum([0])


class TestParseRommTimestamp:
    def test_reads_romm_s_whole_second_offset_form(self):
        assert parse_romm_timestamp("2026-10-19T11:50:01+00:00") == datetime(2026, 10, 19, 11, 50, 1, tzinfo=UTC)

    def test_reads_another_offset_as_the_same_instant(self):
        assert parse_romm_timestamp("2026-10-19T13:50:01+02:00") == datetime(2026, 10, 19, 11, 50, 1, tzinfo=UTC)

    def test_reads_a_trailing_z(self):
        assert parse_romm_timestamp("2026-10-19T11:50:01Z") == datetime(2026, 10, 19, 11, 50, 1, tzinfo=UTC)

    def test_reads_a_value_without_an_offset_as_utc(self):
        assert parse_romm_timestamp("2026-10-19T11:50:01") == datetime(2026, 10, 19, 11, 50, 1, tzinfo=UTC)

    @pytest.mark.parametrize("value", [None, "", "yesterday", 1760874601, ["2026-10-19T11:50:01+00:00"]])
    def test_anything_else_is_none(self, value):
        assert parse_romm_timestamp(value) is None


class TestSkipReference:
    def test_is_the_fetch_start_less_the_margin(self):
        assert SKIP_REFERENCE_MARGIN.total_seconds() == 600
        assert skip_reference(_stamp()) == datetime(2026, 10, 19, 11, 50, tzinfo=UTC)

    def test_a_stamp_without_a_fetch_start_has_none(self):
        assert skip_reference(_stamp(started=None)) is None


class TestStampCanMatch:
    def test_a_stamp_with_both_can(self):
        assert stamp_can_match(_stamp()) is True

    def test_a_stamp_without_a_checksum_cannot(self):
        assert stamp_can_match(_stamp(checksum=None)) is False

    def test_a_stamp_without_a_fetch_start_cannot(self):
        assert stamp_can_match(_stamp(started=None)) is False


class TestProbeVerdict:
    def test_nothing_newer_and_the_same_ids_match(self):
        assert probe_verdict(_stamp(), _answer("2026-10-19T11:00:00+00:00", [3, 1, 2])).unchanged is True

    def test_a_rom_updated_exactly_at_the_reference_matches(self):
        assert probe_verdict(_stamp(), _answer(_REFERENCE, [1, 2, 3])).unchanged is True

    def test_a_rom_updated_one_second_after_the_reference_does_not_match(self):
        assert probe_verdict(_stamp(), _answer("2026-10-19T11:50:01+00:00", [1, 2, 3])).unchanged is False

    def test_a_rom_updated_after_the_fetch_started_does_not_match(self):
        """The fetch-to-completion window (#743 D1): an edit after the fetch began, before the run ended."""
        assert probe_verdict(_stamp(), _answer("2026-10-19T12:20:00+00:00", [1, 2, 3])).unchanged is False

    def test_a_hidden_rom_does_not_match(self):
        """A hide moves no ``updated_at``; only the id set shows it."""
        assert probe_verdict(_stamp(), _answer("2026-10-19T11:00:00+00:00", [1, 2])).unchanged is False

    def test_as_many_added_as_removed_does_not_match(self):
        assert probe_verdict(_stamp(), _answer("2026-10-19T11:00:00+00:00", [1, 2, 4])).unchanged is False

    def test_a_stamp_without_a_checksum_never_matches(self):
        assert probe_verdict(_stamp(checksum=None), _answer("2026-10-19T11:00:00+00:00", [1, 2, 3])).unchanged is False

    def test_a_stamp_without_a_fetch_start_never_matches(self):
        assert probe_verdict(_stamp(started=None), _answer("2026-10-19T11:00:00+00:00", [1, 2, 3])).unchanged is False

    def test_an_empty_platform_matches_a_stamp_of_no_ids(self):
        assert probe_verdict(_stamp(ids=()), _answer(None, [])).unchanged is True

    def test_an_emptied_platform_does_not_match(self):
        assert probe_verdict(_stamp(), _answer(None, [])).unchanged is False

    @pytest.mark.parametrize(
        "answer",
        [
            None,
            [],
            {"items": [], "total": 0},
            {"items": [{"id": 1, "updated_at": "2026-10-19T11:00:00+00:00"}], "total": 3},
            {"rom_id_index": [1, 2, 3], "total": 3},
            {"items": [{"id": 1}], "total": 3, "rom_id_index": [1, 2, 3]},
            {"items": [{"id": 1, "updated_at": "soon"}], "total": 3, "rom_id_index": [1, 2, 3]},
            {"items": ["1"], "total": 3, "rom_id_index": [1, 2, 3]},
            {"items": [{"id": 1, "updated_at": "2026-10-19T11:00:00+00:00"}], "rom_id_index": [1, "2", 3]},
            {"items": [{"id": 1, "updated_at": "2026-10-19T11:00:00+00:00"}], "rom_id_index": [1, 2.0, 3]},
        ],
    )
    def test_an_answer_it_cannot_read_does_not_match(self, answer):
        assert probe_verdict(_stamp(), answer).unchanged is False
        assert probe_verdict(_stamp(), answer).reason == "RomM's answer could not be read"


class TestProbeVerdictReason:
    """The reason the sync log gives for a skip or a full fetch."""

    def test_a_newer_rom_names_its_updated_at_and_the_reference(self):
        verdict = probe_verdict(_stamp(), _answer("2026-10-19T11:50:01+00:00", [1, 2, 3]))
        assert verdict.reason == (
            "newest updated_at 2026-10-19T11:50:01+00:00 is after the reference 2026-10-19T11:50:00+00:00; "
            "3 visible ids, checksum matches the stamp's"
        )

    def test_another_id_set_names_its_size_and_the_checksum_differing(self):
        verdict = probe_verdict(_stamp(), _answer("2026-10-19T11:00:00+00:00", [1, 2]))
        assert verdict.reason == (
            "newest updated_at 2026-10-19T11:00:00+00:00 is not after the reference 2026-10-19T11:50:00+00:00; "
            "2 visible ids, checksum differs from the stamp's"
        )

    def test_a_repeated_id_counts_once(self):
        verdict = probe_verdict(_stamp(), _answer("2026-10-19T11:00:00+00:00", [1, 2, 2, 3]))
        assert verdict.unchanged is True
        assert verdict.reason.endswith("; 3 visible ids, checksum matches the stamp's")

    def test_a_platform_with_no_roms_says_so(self):
        verdict = probe_verdict(_stamp(), _answer(None, []))
        assert verdict.reason == "no visible ROMs; 0 visible ids, checksum differs from the stamp's"

    def test_a_stamp_it_cannot_compare_says_so(self):
        verdict = probe_verdict(_stamp(checksum=None), _answer("2026-10-19T11:00:00+00:00", [1, 2, 3]))
        assert verdict.reason == "the stamp records no fetch start or checksum"


def _rom(rom_id: int, updated_at: str | None = "2026-10-19T11:00:00+00:00") -> dict[str, Any]:
    return {"id": rom_id, "updated_at": updated_at}


class TestRestampDue:
    def test_no_stamp_is_due(self):
        assert restamp_due(None, [_rom(1)]) is True

    def test_a_matching_stamp_is_not_due(self):
        assert restamp_due(_stamp(), [_rom(1), _rom(2), _rom(3)]) is False

    def test_a_stamp_without_a_checksum_is_due(self):
        assert restamp_due(_stamp(checksum=None), [_rom(1), _rom(2), _rom(3)]) is True

    def test_a_stamp_without_a_fetch_start_is_due(self):
        assert restamp_due(_stamp(started=None), [_rom(1), _rom(2), _rom(3)]) is True

    def test_other_ids_are_due(self):
        assert restamp_due(_stamp(), [_rom(1), _rom(2)]) is True

    def test_a_rom_updated_after_the_reference_is_due(self):
        assert restamp_due(_stamp(), [_rom(1), _rom(2, "2026-10-19T11:55:00+00:00"), _rom(3)]) is True

    def test_a_rom_without_a_readable_updated_at_is_due(self):
        assert restamp_due(_stamp(), [_rom(1), _rom(2, None), _rom(3)]) is True

    def test_an_empty_fetch_matching_a_stamp_of_no_ids_is_not_due(self):
        assert restamp_due(_stamp(ids=()), []) is False

    @pytest.mark.parametrize(
        "rom", [{"updated_at": "2026-10-19T11:00:00+00:00"}, {"id": "3", "updated_at": "2026-10-19T11:00:00+00:00"}]
    )
    def test_a_rom_without_an_int_id_is_due(self, rom):
        assert restamp_due(_stamp(), [_rom(1), _rom(2), rom]) is True
