"""Tests for domain.update_outcome — the installer's record, and which update a start announces."""

from __future__ import annotations

import json

import pytest

from domain.update_outcome import UpdateFailure, announced_update, decode_update_failure, standing_update_failure

_RECORD = {"attempted_version": "1.3.0", "restored_version": "1.2.3", "rolled_back_at": "2026-09-25T10:15:00Z"}
_FAILURE = UpdateFailure(attempted_version="1.3.0", restored_version="1.2.3", rolled_back_at="2026-09-25T10:15:00Z")


class TestDecodeUpdateFailure:
    def test_reads_the_installer_s_record(self):
        assert decode_update_failure(json.dumps(_RECORD)) == _FAILURE

    def test_reads_it_as_the_installer_prints_it(self):
        """``record_update_failure`` in install.sh writes one line with a space after each colon."""
        raw = '{"attempted_version": "1.3.0", "restored_version": "1.2.3", "rolled_back_at": "2026-09-25T10:15:00Z"}\n'
        assert decode_update_failure(raw) == _FAILURE

    def test_a_key_it_does_not_know_is_no_reason_to_refuse_the_record(self):
        assert decode_update_failure(json.dumps({**_RECORD, "reason": "timeout"})) == _FAILURE

    @pytest.mark.parametrize("raw", ["", "{not json", "[]", '"1.3.0"', "null", "{}"])
    def test_something_that_is_not_a_record_is_none(self, raw):
        assert decode_update_failure(raw) is None

    @pytest.mark.parametrize("key", ["attempted_version", "restored_version", "rolled_back_at"])
    @pytest.mark.parametrize("value", [None, "", "  ", 130, ["1.3.0"]])
    def test_a_record_short_of_any_key_is_no_record(self, key, value):
        """Half an update on a card would state something the installer did not."""
        assert decode_update_failure(json.dumps({**_RECORD, key: value})) is None

    @pytest.mark.parametrize("key", ["attempted_version", "restored_version", "rolled_back_at"])
    def test_a_record_missing_any_key_is_no_record(self, key):
        record = {name: value for name, value in _RECORD.items() if name != key}
        assert decode_update_failure(json.dumps(record)) is None


class TestStandingUpdateFailure:
    def test_a_record_stands_while_the_version_it_restored_is_running(self):
        assert standing_update_failure(_FAILURE, "1.2.3") == _FAILURE

    @pytest.mark.parametrize("running", ["1.3.0", "1.4.0", "1.2.2"])
    def test_a_record_is_a_leftover_on_any_other_version(self, running):
        """Its attempted version included: an update to it that went through left the record behind."""
        assert standing_update_failure(_FAILURE, running) is None

    def test_no_record_is_none(self):
        assert standing_update_failure(None, "1.2.3") is None


class TestAnnouncedUpdate:
    def test_a_start_on_a_new_version_announces_it(self):
        assert announced_update("1.2.3", "1.3.0", None) == "1.3.0"

    def test_a_start_on_the_same_version_announces_nothing(self):
        assert announced_update("1.3.0", "1.3.0", None) is None

    def test_the_first_start_that_records_a_version_announces_nothing(self):
        assert announced_update(None, "1.3.0", None) is None

    def test_any_change_of_version_is_announced(self):
        """The version is compared for equality: which way it moved is not this rule's question."""
        assert announced_update("1.3.0", "1.2.3", None) == "1.2.3"

    def test_a_start_the_record_calls_a_rollback_announces_nothing(self):
        """Back on 1.2.3 after trying 1.3.0: not an update, whatever the stored version says."""
        assert announced_update("1.3.0", "1.2.3", _FAILURE) is None

    def test_a_record_of_another_rollback_does_not_silence_a_real_update(self):
        """The record stands until the next update that answers; an update past it is still one."""
        assert announced_update("1.2.3", "1.4.0", _FAILURE) == "1.4.0"

    def test_a_record_naming_another_attempt_does_not_silence_this_one(self):
        failure = UpdateFailure(attempted_version="1.2.9", restored_version="1.2.3", rolled_back_at="t")
        assert announced_update("1.3.0", "1.2.3", failure) == "1.2.3"
