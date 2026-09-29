"""Unit tests for the ``PlatformSyncState`` aggregate."""

from __future__ import annotations

from dataclasses import replace

import pytest

from domain.platform_sync_state import PlatformSyncState, stamp_for_skip


class TestStamp:
    def test_sets_all_fields(self):
        stamp = PlatformSyncState.stamp(
            platform_slug="n64",
            at="2026-05-28T10:00:00+00:00",
            rom_count=2091,
        )
        assert stamp.platform_slug == "n64"
        assert stamp.completed_at == "2026-05-28T10:00:00+00:00"
        assert stamp.rom_count == 2091
        assert stamp.skip_revoked is False

    def test_zero_rom_count_allowed(self):
        stamp = PlatformSyncState.stamp(platform_slug="n64", at="2026-05-28T10:00:00+00:00", rom_count=0)
        assert stamp.rom_count == 0

    def test_empty_platform_slug_raises(self):
        with pytest.raises(ValueError, match="platform_slug is required"):
            PlatformSyncState.stamp(platform_slug="", at="2026-05-28T10:00:00+00:00", rom_count=1)

    def test_negative_rom_count_raises(self):
        with pytest.raises(ValueError, match="rom_count must be non-negative"):
            PlatformSyncState.stamp(platform_slug="n64", at="2026-05-28T10:00:00+00:00", rom_count=-1)


class TestStampForSkip:
    def _stamp(self) -> PlatformSyncState:
        return PlatformSyncState.stamp(platform_slug="n64", at="2026-05-28T10:00:00+00:00", rom_count=3, fetch_id="r1")

    def test_a_stamp_that_was_not_revoked_is_returned_as_it_is(self):
        stamp = self._stamp()
        assert stamp_for_skip(stamp) is stamp

    def test_a_revoked_stamp_reads_as_no_stamp(self):
        assert stamp_for_skip(replace(self._stamp(), skip_revoked=True)) is None

    def test_no_stamp_stays_no_stamp(self):
        assert stamp_for_skip(None) is None
