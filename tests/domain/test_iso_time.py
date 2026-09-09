"""Unit tests for domain.iso_time — timestamp parsing and rendering."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta, timezone

import pytest

from domain.iso_time import epoch_to_iso, epoch_to_local_stamp, parse_iso, parse_iso_to_epoch


@pytest.fixture
def berlin(monkeypatch):
    """Run the test two hours off UTC, so a local rendering cannot pass as a UTC one.

    ``tzset`` is what the C library reads ``TZ`` through, and it is called
    again on the way out: ``monkeypatch`` restores the variable, and without
    that second call the process would keep the zone for every later test.
    """
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


class TestParseIso:
    def test_parses_z_suffix(self):
        dt = parse_iso("2024-01-15T12:00:00Z")
        assert dt is not None
        assert dt == datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        assert dt.tzinfo is not None

    def test_parses_explicit_utc_offset(self):
        dt = parse_iso("2024-01-15T12:00:00+00:00")
        assert dt is not None
        assert dt == datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)

    def test_parses_microseconds(self):
        dt = parse_iso("2024-01-15T12:00:00.123456+00:00")
        assert dt is not None
        assert dt == datetime(2024, 1, 15, 12, 0, 0, 123456, tzinfo=UTC)

    def test_parses_non_utc_offset(self):
        dt = parse_iso("2024-01-15T12:00:00+02:00")
        assert dt is not None
        # Same instant in UTC = 10:00
        assert dt.utcoffset() == timedelta(hours=2)
        assert dt.astimezone(UTC) == datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

    def test_parses_negative_offset(self):
        dt = parse_iso("2024-01-15T12:00:00-05:00")
        assert dt is not None
        assert dt.utcoffset() == timedelta(hours=-5)

    def test_empty_string_returns_none(self):
        assert parse_iso("") is None

    def test_none_returns_none(self):
        assert parse_iso(None) is None

    def test_garbage_returns_none(self):
        assert parse_iso("not-a-date") is None

    def test_invalid_components_return_none(self):
        # Month 13, day 99, hour 99 etc — all out of range
        assert parse_iso("2024-13-99T99:99:99Z") is None

    def test_naive_iso_returns_naive_datetime(self):
        # Documented behavior: helper does NOT force a timezone.
        # datetime.fromisoformat("2024-01-15T12:00:00") returns naive dt;
        # callers that need an aware dt must replace tzinfo themselves.
        dt = parse_iso("2024-01-15T12:00:00")
        assert dt is not None
        assert dt.tzinfo is None
        assert dt == datetime(2024, 1, 15, 12, 0, 0)

    def test_only_z_suffix_normalisation(self):
        """Trailing Z (any case) is normalised to +00:00. Lowercase 'z' is not
        a documented input format; we test only uppercase Z (the contract)."""
        dt = parse_iso("2024-12-31T23:59:59Z")
        assert dt is not None
        assert dt.tzinfo is not None


class TestParseIsoToEpoch:
    def test_z_suffix_epoch(self):
        # 2024-01-15T12:00:00Z = 1705320000
        assert parse_iso_to_epoch("2024-01-15T12:00:00Z") == 1705320000.0

    def test_with_offset(self):
        # 2024-01-15T14:00:00+02:00 == 12:00 UTC == 1705320000
        assert parse_iso_to_epoch("2024-01-15T14:00:00+02:00") == 1705320000.0

    def test_microseconds_included_in_epoch(self):
        v = parse_iso_to_epoch("2024-01-15T12:00:00.500000+00:00")
        assert v is not None
        assert abs(v - 1705320000.5) < 1e-6

    def test_empty_returns_none(self):
        assert parse_iso_to_epoch("") is None

    def test_none_returns_none(self):
        assert parse_iso_to_epoch(None) is None

    def test_garbage_returns_none(self):
        assert parse_iso_to_epoch("not-a-date") is None

    def test_consistency_with_parse_iso(self):
        """parse_iso_to_epoch(x) must equal parse_iso(x).timestamp() when not None."""
        for value in (
            "2024-01-15T12:00:00Z",
            "2024-01-15T12:00:00+00:00",
            "2024-01-15T12:00:00.123456+00:00",
            "2024-06-30T08:30:45-07:00",
        ):
            dt = parse_iso(value)
            epoch = parse_iso_to_epoch(value)
            assert dt is not None
            assert epoch is not None
            assert epoch == dt.timestamp()

    def test_naive_iso_uses_local_time(self):
        """Naive ISO -> naive datetime; .timestamp() interprets it as local time.

        We don't pin a specific epoch (CI tz varies); we just assert it
        round-trips through parse_iso consistently."""
        v_naive = "2024-01-15T12:00:00"
        epoch = parse_iso_to_epoch(v_naive)
        dt = parse_iso(v_naive)
        assert epoch is not None
        assert dt is not None
        assert epoch == dt.timestamp()

    def test_failure_chain_returns_none(self):
        """If parse_iso returns None, parse_iso_to_epoch must too."""
        for bad in ("", None, "garbage", "2024-13-01T00:00:00Z"):
            assert parse_iso_to_epoch(bad) is None
            assert parse_iso(bad) is None


class TestEpochToIso:
    def test_epoch_zero_is_unix_epoch_utc(self):
        assert epoch_to_iso(0.0) == "1970-01-01T00:00:00+00:00"

    def test_known_nonzero_epoch(self):
        # 1705320000.0 == 2024-01-15T12:00:00Z
        assert epoch_to_iso(1705320000.0) == "2024-01-15T12:00:00+00:00"

    def test_fractional_seconds_render_microseconds(self):
        assert epoch_to_iso(1705320000.5) == "2024-01-15T12:00:00.500000+00:00"

    def test_always_carries_utc_offset(self):
        assert epoch_to_iso(1705320000.0).endswith("+00:00")

    def test_round_trips_through_parse_iso_to_epoch(self):
        """parse_iso_to_epoch(epoch_to_iso(e)) == e for concrete instants."""
        for e in (0.0, 1705320000.0, 1705320000.5):
            assert parse_iso_to_epoch(epoch_to_iso(e)) == e


class TestEdgeOffsets:
    def test_far_future_utc(self):
        dt = parse_iso("2099-12-31T23:59:59+00:00")
        assert dt is not None
        assert dt.tzinfo == UTC

    def test_unusual_offset(self):
        # +05:45 (Nepal) — not a multiple-of-hour offset
        dt = parse_iso("2024-01-15T12:00:00+05:45")
        assert dt is not None
        assert dt.utcoffset() == timedelta(hours=5, minutes=45)
        # Compare with a manual aware dt for equality
        expected = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=45)))
        assert dt == expected


class TestEpochToLocalStamp:
    """The rendering for text a person reads, not for anything that parses it back."""

    def test_renders_the_instant_in_the_machines_own_zone(self, berlin):
        assert epoch_to_local_stamp(datetime(2026, 9, 4, 12, 0, tzinfo=UTC).timestamp()) == (
            "4 September 2026 at 14:00"
        )

    def test_the_day_is_not_padded(self, berlin):
        assert epoch_to_local_stamp(datetime(2026, 9, 1, 6, 30, tzinfo=UTC).timestamp()).startswith("1 September")

    def test_a_zone_change_moves_the_reading_and_epoch_to_iso_never_does(self, berlin, monkeypatch):
        """The two renderings answer different questions, and only one follows the reader.

        ``epoch_to_iso`` is on the wire and is parsed back, so it stays UTC
        whatever the machine is set to.
        """
        instant = datetime(2026, 9, 4, 12, 0, tzinfo=UTC).timestamp()
        in_berlin = epoch_to_local_stamp(instant)

        monkeypatch.setenv("TZ", "UTC")
        time.tzset()

        assert epoch_to_local_stamp(instant) == "4 September 2026 at 12:00"
        assert in_berlin == "4 September 2026 at 14:00"
        assert epoch_to_iso(instant).startswith("2026-09-04T12:00")

    def test_it_carries_no_seconds_offset_or_t_separator(self, berlin):
        """The shape a reader was given instead of ``2026-09-09T11:58:19.009461+00:00``."""
        rendered = epoch_to_local_stamp(datetime(2026, 9, 4, 12, 0, 19, 9461, tzinfo=UTC).timestamp())

        assert rendered == "4 September 2026 at 14:00"
        assert "T" not in rendered
        assert "+" not in rendered
