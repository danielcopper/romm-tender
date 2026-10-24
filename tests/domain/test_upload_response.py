"""Tests for backend/domain/upload_response.py — what RomM's answer to a save upload says."""

from __future__ import annotations

from domain.upload_response import dedup_returned_non_head

_DEDUP_GROUP = [
    {"id": 100, "updated_at": "2026-02-17T06:00:00Z"},  # older
    {"id": 200, "updated_at": "2026-03-01T00:00:00Z"},  # newest = head
]


class TestDedupReturnedNonHead:
    """`dedup_returned_non_head` — pure id-based detection of an ``add_save``
    POST that deduped to a pre-existing NON-head save (#1482)."""

    def test_empty_planned_group_is_benign(self):
        """No planned head to bypass → benign."""
        assert dedup_returned_non_head({"id": 100}, []) is False

    def test_missing_response_id_is_benign(self):
        assert dedup_returned_non_head({}, _DEDUP_GROUP) is False

    def test_response_is_the_head_is_benign(self):
        """Dedup returned the newest save in the snapshot → benign."""
        assert dedup_returned_non_head({"id": 200}, _DEDUP_GROUP) is False

    def test_new_version_id_absent_from_snapshot_is_benign(self):
        """A freshly minted version (id not in the pre-upload snapshot) → benign."""
        assert dedup_returned_non_head({"id": 999}, _DEDUP_GROUP) is False

    def test_pre_existing_non_head_is_flagged(self):
        """Dedup returned the older 100 while 200 still leads the slot → flagged."""
        assert dedup_returned_non_head({"id": 100}, _DEDUP_GROUP) is True
