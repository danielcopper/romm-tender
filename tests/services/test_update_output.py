"""Tests for UpdateOutputService — which journal run belongs to a failed update, and what of it is answered.

The journal is a :class:`FakeJournal` holding synthetic lines; nothing here
reads this machine's.
"""

from __future__ import annotations

import logging

import pytest
from fakes.fake_journal import FakeJournal
from fakes.running_loop import running_loop

from domain.update_install import INSTALLER_UNIT
from domain.update_outcome import UpdateFailure, UpdateFailureKind
from domain.update_output import REPLACED_RUN_LINES, SERVICE_UNIT, JournalEntry, utc_stamp_seconds
from services.update_output import UpdateOutputService, UpdateOutputServiceConfig

_RUNNING = "1.0.31"
_TRIED = "1.0.32"
_STAMP = "2026-09-30T11:03:20Z"
_AT = utc_stamp_seconds(_STAMP) or 0.0
_ADDRESS = (
    "host: load the panel from "
    "http://127.0.0.1:27737/index.js?token=Zq3_x-9Ab0cDeFgHiJkLmNoPqRsTuVwXyZ01234"  # gitleaks:allow
)


def _record(kind: UpdateFailureKind = UpdateFailureKind.ROLLBACK, stamp: str = _STAMP) -> UpdateFailure:
    return UpdateFailure(attempted_version=_TRIED, restored_version=_RUNNING, rolled_back_at=stamp, kind=kind)


def _line(unit: str, at: float, invocation: str | None, message: str) -> tuple[str, JournalEntry]:
    return unit, JournalEntry(at=at, invocation=invocation, message=message)


def _make(
    logger: logging.Logger,
    journal: FakeJournal,
    *,
    record: UpdateFailure | None = None,
    started_at: str | None = None,
) -> UpdateOutputService:
    return UpdateOutputService(
        config=UpdateOutputServiceConfig(
            current_version=_RUNNING,
            read_update_failure=lambda: record,
            failed_installer_started_at=lambda: started_at,
            journal=journal,
            loop=running_loop(),
            logger=logger,
        )
    )


# A rollback as the journal holds it: an older installer run, the old
# backend's run that the installer stops, the installer's run around the
# record's stamp, the failed version's run in between, and the restored
# version's start after the stamp.
_ROLLBACK_JOURNAL = [
    _line(INSTALLER_UNIT, _AT - 3000, "older", "[ok] an earlier update"),
    _line(SERVICE_UNIT, _AT - 900, "old", "old backend running"),
    _line(INSTALLER_UNIT, _AT - 120, "inst", "[..] Service      waiting for 1.0.32 to answer"),
    _line(SERVICE_UNIT, _AT - 110, "old", "old backend shutting down"),
    _line(SERVICE_UNIT, _AT - 100, "new", "Traceback (most recent call last):"),
    _line(SERVICE_UNIT, _AT - 99, "new", _ADDRESS),
    _line(SERVICE_UNIT, _AT - 98, "new", "SystemExit: deliberately broken test build 1.0.32"),
    _line(INSTALLER_UNIT, _AT + 5, "inst", "[!!] Service      update to 1.0.32 failed; back on 1.0.31"),
    _line(SERVICE_UNIT, _AT + 6, "restored", "restored backend starting"),
    _line(INSTALLER_UNIT, _AT + 4000, "later", "[ok] a later run"),
]


class TestAfterARollback:
    async def test_answers_the_installer_s_run_and_what_the_failed_version_printed(self, logger):
        journal = FakeJournal(_ROLLBACK_JOURNAL)
        service = _make(logger, journal, record=_record())

        answer = await service.get_update_output(_STAMP)

        assert answer == {
            "success": True,
            "ran_at": _AT - 120,
            "installer": {
                "lines": ["[!!] Service      update to 1.0.32 failed; back on 1.0.31"],
                "earlier": 0,
            },
            "new_version": {
                "lines": [
                    "Traceback (most recent call last):",
                    "host: load the panel from http://127.0.0.1:27737/index.js?token=[hidden]",
                    "SystemExit: deliberately broken test build 1.0.32",
                ],
                "earlier": 0,
            },
            "missing": None,
        }

    async def test_the_failed_version_s_window_runs_from_the_installer_s_start_to_the_stamp(self, logger):
        journal = FakeJournal(_ROLLBACK_JOURNAL)
        service = _make(logger, journal, record=_record())

        await service.get_update_output(_STAMP)

        assert (SERVICE_UNIT, _AT - 120, _AT, None) in journal.reads
        assert (SERVICE_UNIT, None, _AT - 120, REPLACED_RUN_LINES) in journal.reads

    async def test_a_failed_version_that_printed_nothing_has_no_section(self, logger):
        journal = FakeJournal([entry for entry in _ROLLBACK_JOURNAL if entry[1].invocation != "new"])
        service = _make(logger, journal, record=_record())

        answer = await service.get_update_output(_STAMP)

        assert answer["new_version"] is None
        assert answer["installer"] is not None

    async def test_a_line_of_no_run_just_before_the_installer_does_not_let_the_replaced_version_s_lines_in(
        self, logger
    ):
        journal = FakeJournal([*_ROLLBACK_JOURNAL, _line(SERVICE_UNIT, _AT - 121, None, "Failed to open unit file")])
        service = _make(logger, journal, record=_record())

        answer = await service.get_update_output(_STAMP)

        assert "old backend shutting down" not in answer["new_version"]["lines"]
        assert answer["new_version"]["lines"][0] == "Traceback (most recent call last):"

    async def test_with_nothing_running_before_the_installer_every_run_in_the_window_is_the_failed_version_s(
        self, logger
    ):
        journal = FakeJournal([entry for entry in _ROLLBACK_JOURNAL if entry[1].invocation != "old"])
        service = _make(logger, journal, record=_record())

        answer = await service.get_update_output(_STAMP)

        assert answer["new_version"]["lines"][0] == "Traceback (most recent call last):"


class TestAPreInstallCheckRefusal:
    @pytest.mark.parametrize("kind", [UpdateFailureKind.CHECK, UpdateFailureKind.UNKNOWN])
    async def test_answers_the_installer_s_run_alone(self, logger, kind):
        journal = FakeJournal(_ROLLBACK_JOURNAL)
        service = _make(logger, journal, record=_record(kind))

        answer = await service.get_update_output(_STAMP)

        assert answer["installer"]["lines"] == ["[!!] Service      update to 1.0.32 failed; back on 1.0.31"]
        assert answer["new_version"] is None
        assert all(read[0] == INSTALLER_UNIT for read in journal.reads)


class TestARecordWithNoRunInTheJournal:
    async def test_a_journal_that_reaches_back_that_far_says_the_installer_ran_in_a_terminal(self, logger):
        journal = FakeJournal([_line("plasma", _AT - 10000, "x", "something else entirely")])
        service = _make(logger, journal, record=_record())

        answer = await service.get_update_output(_STAMP)

        assert answer == {
            "success": True,
            "ran_at": None,
            "installer": None,
            "new_version": None,
            "missing": "terminal",
        }

    async def test_a_journal_that_begins_after_the_failure_says_it_is_no_longer_there(self, logger):
        journal = FakeJournal([_line("plasma", _AT + 10, "x", "logged after the failure")])
        service = _make(logger, journal, record=_record())

        answer = await service.get_update_output(_STAMP)

        assert answer["missing"] == "rotated"
        assert answer["installer"] is None

    async def test_the_latest_run_is_never_taken_for_the_record_s(self, logger):
        journal = FakeJournal(
            [
                _line("plasma", _AT - 10000, "x", "the journal reaches back"),
                _line(INSTALLER_UNIT, _AT + 60, "later", "[..] a run after the record"),
            ]
        )
        service = _make(logger, journal, record=_record())

        assert (await service.get_update_output(_STAMP))["missing"] == "terminal"


class TestAnAttemptOfThisProcess:
    async def test_answers_the_first_installer_run_that_began_at_or_after_its_start(self, logger):
        started_at = "2026-09-30T20:58:00Z"
        at = utc_stamp_seconds(started_at) or 0.0
        journal = FakeJournal(
            [
                _line(INSTALLER_UNIT, at - 500, "older", "an earlier run"),
                _line(INSTALLER_UNIT, at + 1, "mine", "Started [systemd-run] /bin/bash install.sh"),
                _line(INSTALLER_UNIT, at + 13, "mine", "[!!] Installing   the new version does not start"),
                _line(INSTALLER_UNIT, at + 900, "later", "a later run"),
            ]
        )
        service = _make(logger, journal, started_at=started_at)

        answer = await service.get_update_output(None)

        assert answer["installer"]["lines"] == [
            "Started [systemd-run] /bin/bash install.sh",
            "[!!] Installing   the new version does not start",
        ]
        assert answer["ran_at"] == at + 1
        assert answer["new_version"] is None

    async def test_an_attempt_whose_run_is_not_in_the_journal_any_more_says_so(self, logger):
        service = _make(logger, FakeJournal(), started_at="2026-09-30T20:58:00Z")

        assert (await service.get_update_output(None))["missing"] == "rotated"

    async def test_an_attempt_whose_run_was_never_written_where_the_journal_reaches_back_says_so(self, logger):
        started_at = "2026-09-30T20:58:00Z"
        at = utc_stamp_seconds(started_at) or 0.0
        journal = FakeJournal([_line(SERVICE_UNIT, at - 1, "backend", "update: starting the installer for 1.0.32")])
        service = _make(logger, journal, started_at=started_at)

        answer = await service.get_update_output(None)

        assert answer == {"success": True, "ran_at": None, "installer": None, "new_version": None, "missing": "empty"}

    async def test_no_attempt_whose_installer_ran_is_nothing_to_show(self, logger):
        journal = FakeJournal()
        service = _make(logger, journal, started_at=None)

        answer = await service.get_update_output(None)

        assert answer["success"] is False
        assert answer["reason"] == "not_found"
        assert journal.reads == []


class TestWhatIsNotAnswered:
    async def test_a_stamp_that_is_not_the_standing_record_s_is_nothing_to_show(self, logger):
        journal = FakeJournal(_ROLLBACK_JOURNAL)
        service = _make(logger, journal, record=_record())

        answer = await service.get_update_output("2026-09-29T08:00:00Z")

        assert (answer["success"], answer["reason"]) == (False, "not_found")
        assert journal.reads == []

    async def test_a_record_that_no_longer_stands_is_nothing_to_show(self, logger):
        leftover = UpdateFailure(_TRIED, "0.9.0", _STAMP)
        service = _make(logger, FakeJournal(_ROLLBACK_JOURNAL), record=leftover)

        assert (await service.get_update_output(_STAMP))["reason"] == "not_found"

    async def test_no_record_at_all_is_nothing_to_show(self, logger):
        service = _make(logger, FakeJournal(_ROLLBACK_JOURNAL))

        assert (await service.get_update_output(_STAMP))["reason"] == "not_found"

    async def test_nothing_to_show_is_logged_with_what_was_asked_about(self, logger, caplog):
        service = _make(logger, FakeJournal(_ROLLBACK_JOURNAL))

        with caplog.at_level(logging.INFO, logger=logger.name):
            await service.get_update_output(_STAMP)

        assert f"no standing record is stamped {_STAMP}" in caplog.text

    @pytest.mark.parametrize("value", [7, ["2026"], {"at": 1}, True])
    async def test_an_argument_that_is_neither_a_stamp_nor_none_is_refused_and_logged(self, logger, caplog, value):
        with caplog.at_level(logging.WARNING, logger=logger.name):
            answer = await _make(logger, FakeJournal()).get_update_output(value)

        assert answer == {"success": False, "reason": "invalid_value", "message": "Invalid record"}
        assert repr(value) in caplog.text

    async def test_a_journal_that_cannot_be_read_is_said_and_logged(self, logger, caplog):
        service = _make(logger, FakeJournal(raises=OSError("journalctl exited with status 1")), record=_record())

        with caplog.at_level(logging.WARNING, logger=logger.name):
            answer = await service.get_update_output(_STAMP)

        assert answer == {"success": False, "reason": "journal_unreadable", "message": "The journal could not be read"}
        assert "the journal could not be read" in caplog.text

    async def test_a_journal_that_gave_no_answer_in_time_is_one_that_could_not_be_read(self, logger):
        service = _make(logger, FakeJournal(raises=TimeoutError("journalctl did not answer")), started_at=_STAMP)

        assert (await service.get_update_output(None))["reason"] == "journal_unreadable"
