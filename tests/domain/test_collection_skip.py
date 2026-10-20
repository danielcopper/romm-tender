"""Tests for ``domain/collection_skip.py`` — what a collection's stamp records and what RomM must answer to match it."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain.collection_skip import membership_verdict, probe_verdict, recorded_updated_at
from domain.collection_sync_state import CollectionSyncState
from domain.platform_skip import SKIP_REFERENCE_MARGIN, rom_id_checksum, skip_reference

_FETCH_START = "2026-10-19T12:00:00+00:00"
_UPDATED_AT = "2026-10-01T09:00:00+00:00"


def _stamp(
    *,
    kind: str = "standard",
    listing_ids: tuple[int, ...] | None = (10, 11),
    member_rom_ids: tuple[int, ...] = (10, 11),
    fetch_started_at: str | None = _FETCH_START,
    updated_at: str = _UPDATED_AT,
) -> CollectionSyncState:
    return CollectionSyncState.stamp(
        collection_id="7",
        collection_kind=kind,
        updated_at="" if kind == "virtual" else updated_at,
        completed_at="2026-10-19T12:30:00+00:00",
        rom_count=len(member_rom_ids),
        member_rom_ids=member_rom_ids,
        fetch_started_at=fetch_started_at,
        listing_rom_id_checksum=rom_id_checksum(listing_ids) if listing_ids is not None else None,
    )


class TestRecordedUpdatedAt:
    def test_a_virtual_collection_records_an_empty_one(self):
        assert recorded_updated_at("virtual", None) == ""
        assert recorded_updated_at("virtual", "2026-10-01T09:00:00") == ""

    @pytest.mark.parametrize("kind", ["standard", "smart"])
    def test_a_standard_or_smart_collection_records_the_listing_s(self, kind):
        assert recorded_updated_at(kind, _UPDATED_AT) == _UPDATED_AT

    @pytest.mark.parametrize("kind", ["standard", "smart"])
    @pytest.mark.parametrize("listed", [None, ""])
    def test_a_standard_or_smart_collection_whose_listing_has_none_is_never_stamped(self, kind, listed):
        assert recorded_updated_at(kind, listed) is None


class TestTheReference:
    def test_a_collection_stamp_s_reference_is_its_fetch_start_less_the_margin(self):
        reference = skip_reference(_stamp())

        assert reference == datetime(2026, 10, 19, 12, 0, tzinfo=UTC) - SKIP_REFERENCE_MARGIN
        assert reference == datetime(2026, 10, 19, 11, 50, tzinfo=UTC)

    def test_a_collection_stamp_without_a_fetch_start_has_none(self):
        assert skip_reference(_stamp(fetch_started_at=None)) is None


class TestMembershipVerdict:
    def test_the_listing_the_stamp_recorded_matches(self):
        verdict = membership_verdict(_stamp(), listing_updated_at=_UPDATED_AT, listing_rom_ids=(11, 10, 10))

        assert verdict.unchanged is True
        assert verdict.reason == "2 listed ids, checksum matches the stamp's"

    def test_a_stamp_without_a_fetch_start_never_matches(self):
        verdict = membership_verdict(
            _stamp(fetch_started_at=None), listing_updated_at=_UPDATED_AT, listing_rom_ids=(10, 11)
        )

        assert verdict.unchanged is False
        assert verdict.reason == "the stamp records no fetch start or checksum"

    def test_a_stamp_without_a_checksum_never_matches(self):
        verdict = membership_verdict(_stamp(listing_ids=None), listing_updated_at=_UPDATED_AT, listing_rom_ids=(10, 11))

        assert verdict.unchanged is False
        assert verdict.reason == "the stamp records no fetch start or checksum"

    def test_an_equal_count_change_to_the_listed_members_does_not_match(self):
        # One member hidden by an admin and another unhidden: same count, other ids.
        verdict = membership_verdict(_stamp(), listing_updated_at=_UPDATED_AT, listing_rom_ids=(10, 12))

        assert verdict.unchanged is False
        assert verdict.reason == "2 listed ids, checksum differs from the stamp's"

    @pytest.mark.parametrize("kind", ["standard", "smart"])
    def test_a_changed_updated_at_does_not_match(self, kind):
        verdict = membership_verdict(
            _stamp(kind=kind), listing_updated_at="2026-10-18T09:00:00+00:00", listing_rom_ids=(10, 11)
        )

        assert verdict.unchanged is False
        assert verdict.reason.startswith("collection updated_at changed")

    def test_a_virtual_collection_s_updated_at_is_not_compared(self):
        verdict = membership_verdict(
            _stamp(kind="virtual"), listing_updated_at="2026-10-19T13:00:00+00:00", listing_rom_ids=(10, 11)
        )

        assert verdict.unchanged is True

    def test_the_ids_a_walk_answered_beyond_the_listing_are_not_compared(self):
        # An own smart collection whose walk returned more ids than its listing.
        stamp = _stamp(kind="smart", listing_ids=(10, 11), member_rom_ids=(10, 11, 12))

        verdict = membership_verdict(stamp, listing_updated_at=_UPDATED_AT, listing_rom_ids=(10, 11))

        assert verdict.unchanged is True

    def test_one_listed_id_is_worded_singular(self):
        verdict = membership_verdict(
            _stamp(listing_ids=(10,), member_rom_ids=(10,)), listing_updated_at=_UPDATED_AT, listing_rom_ids=(10,)
        )

        assert verdict.reason == "1 listed id, checksum matches the stamp's"


_REFERENCE = datetime(2026, 10, 19, 11, 50, tzinfo=UTC)


class TestProbeVerdict:
    def test_no_member_updated_after_the_reference_matches(self):
        verdict = probe_verdict({"items": [], "total": 0}, _REFERENCE)

        assert verdict.unchanged is True
        assert verdict.reason == "no member updated after the reference 2026-10-19T11:50:00+00:00"

    def test_a_member_updated_after_the_reference_does_not_match(self):
        verdict = probe_verdict({"items": [{"id": 10}], "total": 2}, _REFERENCE)

        assert verdict.unchanged is False
        assert verdict.reason == "2 member(s) updated after the reference 2026-10-19T11:50:00+00:00"

    @pytest.mark.parametrize("answer", [None, [], [{"id": 10}], "0", 0])
    def test_an_answer_that_is_not_a_json_object_does_not_match(self, answer):
        verdict = probe_verdict(answer, _REFERENCE)

        assert verdict.unchanged is False
        assert verdict.reason == "RomM's answer could not be read"

    @pytest.mark.parametrize("answer", [{}, {"items": []}, {"total": None}, {"total": "0"}, {"total": False}])
    def test_an_object_without_an_integer_total_does_not_match(self, answer):
        verdict = probe_verdict(answer, _REFERENCE)

        assert verdict.unchanged is False
        assert verdict.reason == "RomM's answer could not be read"


class TestWhetherAFreshStampSettlesTheRefusal:
    """Every refusal of the listing, and a member updated after the reference, a fresh stamp settles (#2477 D3)."""

    @pytest.mark.parametrize(
        ("stamp", "listing_updated_at", "listing_ids"),
        [
            (_stamp(fetch_started_at=None), _UPDATED_AT, (10, 11)),
            (_stamp(listing_ids=None), _UPDATED_AT, (10, 11)),
            (_stamp(), "2026-10-18T09:00:00+00:00", (10, 11)),
            (_stamp(), _UPDATED_AT, (10, 12)),
        ],
        ids=["no fetch start", "no checksum", "updated_at", "listed ids"],
    )
    def test_a_listing_refusal_is_settled_by_a_fresh_stamp(self, stamp, listing_updated_at, listing_ids):
        verdict = membership_verdict(stamp, listing_updated_at=listing_updated_at, listing_rom_ids=listing_ids)

        assert (verdict.unchanged, verdict.restamp_settles) == (False, True)

    def test_a_matching_listing_asks_for_no_stamp(self):
        verdict = membership_verdict(_stamp(), listing_updated_at=_UPDATED_AT, listing_rom_ids=(10, 11))

        assert verdict.restamp_settles is False

    def test_a_member_updated_after_the_reference_is_settled_by_a_fresh_stamp(self):
        assert probe_verdict({"total": 1}, _REFERENCE).restamp_settles is True

    @pytest.mark.parametrize("answer", [[], {"total": None}])
    def test_an_answer_that_could_not_be_read_is_not(self, answer):
        assert probe_verdict(answer, _REFERENCE).restamp_settles is False

    def test_a_probe_with_nothing_updated_asks_for_no_stamp(self):
        assert probe_verdict({"total": 0}, _REFERENCE).restamp_settles is False
