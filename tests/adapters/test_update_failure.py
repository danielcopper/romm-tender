"""Tests for adapters.update_failure — the installer's record, read off the state directory."""

from __future__ import annotations

import json

import pytest

from adapters.update_failure import UpdateFailureFileAdapter
from domain.update_outcome import UPDATE_FAILURE_FILENAME, UpdateFailure

_RECORD = {"attempted_version": "1.3.0", "restored_version": "1.2.3", "rolled_back_at": "2026-09-25T10:15:00Z"}


@pytest.fixture
def log():
    return []


@pytest.fixture
def adapter(tmp_path, log):
    return UpdateFailureFileAdapter(state_dir=str(tmp_path), log_debug=log.append)


def _write(tmp_path, text: str) -> None:
    (tmp_path / UPDATE_FAILURE_FILENAME).write_text(text, encoding="utf-8")


class TestReadUpdateFailure:
    def test_reads_the_record_in_the_state_directory(self, adapter, tmp_path):
        _write(tmp_path, json.dumps(_RECORD) + "\n")

        assert adapter.read_update_failure() == UpdateFailure(
            attempted_version="1.3.0", restored_version="1.2.3", rolled_back_at="2026-09-25T10:15:00Z"
        )

    def test_no_record_is_none_and_says_nothing(self, adapter, log):
        """The ordinary case: no update was ever rolled back, or a later one removed the record."""
        assert adapter.read_update_failure() is None
        assert log == []

    def test_a_record_that_went_away_is_gone_on_the_next_read(self, adapter, tmp_path):
        _write(tmp_path, json.dumps(_RECORD))
        assert adapter.read_update_failure() is not None

        (tmp_path / UPDATE_FAILURE_FILENAME).unlink()

        assert adapter.read_update_failure() is None

    @pytest.mark.parametrize("text", ["", "{half", "[]", json.dumps({**_RECORD, "restored_version": ""})])
    def test_a_malformed_record_is_none_and_reaches_the_debug_log(self, adapter, tmp_path, log, text):
        _write(tmp_path, text)

        assert adapter.read_update_failure() is None
        assert log

    def test_bytes_that_are_not_text_are_none(self, adapter, tmp_path, log):
        (tmp_path / UPDATE_FAILURE_FILENAME).write_bytes(b"\xff\xfe\x00")

        assert adapter.read_update_failure() is None
        assert log

    def test_a_record_that_cannot_be_opened_is_none(self, adapter, tmp_path, log):
        """A directory under the record's name stands in for every open that fails."""
        (tmp_path / UPDATE_FAILURE_FILENAME).mkdir()

        assert adapter.read_update_failure() is None
        assert log
