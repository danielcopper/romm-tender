"""Tests for backend/domain/upload_response.py — what RomM's answer to a save upload says."""

from __future__ import annotations

from domain.upload_response import answer_content_hash, answer_shows_device_not_current, dedup_returned_non_head

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


class TestAnswerShowsDeviceNotCurrent:
    """`answer_shows_device_not_current` — only an entry naming this device says anything."""

    def test_this_device_not_current(self):
        answer = {"device_syncs": [{"device_id": "dev-1", "is_current": False}]}
        assert answer_shows_device_not_current(answer, "dev-1") is True

    def test_this_device_current(self):
        answer = {"device_syncs": [{"device_id": "dev-1", "is_current": True}]}
        assert answer_shows_device_not_current(answer, "dev-1") is False

    def test_an_entry_without_is_current_is_not_current(self):
        assert answer_shows_device_not_current({"device_syncs": [{"device_id": "dev-1"}]}, "dev-1") is True

    def test_only_another_device_named(self):
        answer = {"device_syncs": [{"device_id": "other", "is_current": False}]}
        assert answer_shows_device_not_current(answer, "dev-1") is False

    def test_no_device_syncs(self):
        assert answer_shows_device_not_current({"id": 1}, "dev-1") is False

    def test_device_syncs_not_a_list(self):
        assert answer_shows_device_not_current({"device_syncs": "garbage"}, "dev-1") is False


class TestAnswerContentHash:
    """`answer_content_hash` — the save's own hash as the answer states it, else ``None``."""

    def test_the_stated_hash(self):
        assert answer_content_hash({"content_hash": "abc"}) == "abc"

    def test_no_hash(self):
        assert answer_content_hash({"id": 1}) is None

    def test_a_hash_that_is_not_a_string(self):
        assert answer_content_hash({"content_hash": 123}) is None
