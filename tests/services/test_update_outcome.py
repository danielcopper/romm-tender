"""Tests for UpdateOutcomeService — what the last update did, told once where the user will see it."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWorkFactory
from fakes.running_loop import running_loop

from domain.update_outcome import UpdateFailure, UpdateFailureKind
from services.update_outcome import (
    FAILURE_DISMISSED_KEY,
    LAST_RUN_KEY,
    UpdateOutcomeService,
    UpdateOutcomeServiceConfig,
)

_FAILURE = UpdateFailure(attempted_version="1.3.0", restored_version="1.2.3", rolled_back_at="2026-09-25T10:15:00Z")
_REFUSED = UpdateFailure(
    attempted_version="1.3.0",
    restored_version="1.2.3",
    rolled_back_at="2026-09-25T10:15:00Z",
    kind=UpdateFailureKind.CHECK,
)
_OUTCOME_KEYS = {"announce_version", "announce_direction", "toast_owed", "failure", "failure_dismissed"}


class _Record:
    """The installer's record as the seam answers it; ``failure`` is swapped to stand for the file changing."""

    def __init__(self, failure: UpdateFailure | None = None, raises: Exception | None = None) -> None:
        self.failure = failure
        self.raises = raises
        self.calls = 0

    def __call__(self) -> UpdateFailure | None:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.failure


def _make(
    logger: logging.Logger,
    *,
    running: str = "1.3.0",
    last_run: str | None = None,
    record: _Record | None = None,
    settings: dict[str, Any] | None = None,
    uow_factory: FakeUnitOfWorkFactory | None = None,
):
    factory = uow_factory if uow_factory is not None else FakeUnitOfWorkFactory()
    if last_run is not None:
        with factory() as uow:
            uow.kv_config.set(LAST_RUN_KEY, last_run)
    used_settings = settings if settings is not None else {}
    persister = FakeSettingsPersister()
    service = UpdateOutcomeService(
        config=UpdateOutcomeServiceConfig(
            current_version=running,
            read_update_failure=record if record is not None else _Record(),
            uow_factory=factory,
            settings=used_settings,
            settings_persister=persister,
            loop=running_loop(),
            logger=logger,
        ),
    )
    return service, factory, used_settings, persister


def _last_run(factory: FakeUnitOfWorkFactory) -> str | None:
    with factory() as uow:
        return uow.kv_config.get(LAST_RUN_KEY)


def _lines(caplog: pytest.LogCaptureFixture, logger: logging.Logger, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == logger.name and r.levelno == level]


class TestAStartAfterAnUpdate:
    async def test_announces_the_new_version_as_updated_and_logs_where_it_came_from(self, logger, caplog):
        service, factory, _, _ = _make(logger, running="1.3.0", last_run="1.2.3")

        with caplog.at_level(logging.INFO, logger=logger.name):
            service.note_start()

        outcome = await service.get_update_outcome()
        assert (outcome["announce_version"], outcome["announce_direction"], outcome["toast_owed"]) == (
            "1.3.0",
            "updated",
            True,
        )
        assert _lines(caplog, logger, logging.INFO) == ["updated from 1.2.3 to 1.3.0"]
        assert _last_run(factory) == "1.3.0"

    async def test_a_raised_toast_is_owed_no_more_and_leaves_the_card_standing(self, logger):
        service, _, _, _ = _make(logger, running="1.3.0", last_run="1.2.3")
        service.note_start()

        assert service.acknowledge_update_toast() == {"success": True}

        outcome = await service.get_update_outcome()
        assert (outcome["announce_version"], outcome["announce_direction"], outcome["toast_owed"]) == (
            "1.3.0",
            "updated",
            False,
        )

    async def test_acknowledging_the_toast_twice_is_harmless(self, logger):
        service, _, _, _ = _make(logger, running="1.3.0", last_run="1.2.3")
        service.note_start()
        service.acknowledge_update_toast()

        assert service.acknowledge_update_toast() == {"success": True}
        outcome = await service.get_update_outcome()
        assert (outcome["announce_version"], outcome["toast_owed"]) == ("1.3.0", False)

    async def test_a_dismissed_card_takes_the_announcement_away(self, logger):
        service, _, _, _ = _make(logger, running="1.3.0", last_run="1.2.3")
        service.note_start()
        service.acknowledge_update_toast()

        assert service.dismiss_update_announcement() == {"success": True}

        outcome = await service.get_update_outcome()
        assert (outcome["announce_version"], outcome["announce_direction"], outcome["toast_owed"]) == (
            None,
            None,
            False,
        )

    async def test_a_card_dismissed_before_its_toast_was_raised_owes_no_toast(self, logger):
        """Nothing is left to announce: the wire never owes a toast without a version to name."""
        service, _, _, _ = _make(logger, running="1.3.0", last_run="1.2.3")
        service.note_start()

        service.dismiss_update_announcement()

        assert (await service.get_update_outcome())["toast_owed"] is False
        assert service.acknowledge_update_toast() == {"success": True}
        assert (await service.get_update_outcome())["announce_version"] is None

    async def test_dismissing_twice_is_harmless(self, logger):
        service, _, _, _ = _make(logger, running="1.3.0", last_run="1.2.3")
        service.note_start()
        service.dismiss_update_announcement()

        assert service.dismiss_update_announcement() == {"success": True}
        assert (await service.get_update_outcome())["announce_version"] is None

    async def test_the_next_start_owes_nothing(self, logger, caplog):
        """The version was recorded at the first start, so a second one compares equal."""
        factory = FakeUnitOfWorkFactory()
        _make(logger, running="1.3.0", last_run="1.2.3", uow_factory=factory)[0].note_start()
        again, _, _, _ = _make(logger, running="1.3.0", uow_factory=factory)

        with caplog.at_level(logging.INFO, logger=logger.name):
            again.note_start()

        assert (await again.get_update_outcome())["announce_version"] is None
        assert _lines(caplog, logger, logging.INFO) == []


class TestAStartOnAnEarlierVersion:
    async def test_announces_it_as_back_and_logs_what_it_came_back_from(self, logger, caplog):
        service, factory, _, _ = _make(logger, running="1.2.3", last_run="1.3.0")

        with caplog.at_level(logging.INFO, logger=logger.name):
            service.note_start()

        outcome = await service.get_update_outcome()
        assert (outcome["announce_version"], outcome["announce_direction"], outcome["toast_owed"]) == (
            "1.2.3",
            "back",
            True,
        )
        assert _lines(caplog, logger, logging.INFO) == ["back on 1.2.3 after 1.3.0"]
        assert _last_run(factory) == "1.2.3"

    async def test_its_card_stands_past_the_toast_and_goes_once_dismissed(self, logger):
        service, _, _, _ = _make(logger, running="1.2.3", last_run="1.3.0")
        service.note_start()

        service.acknowledge_update_toast()
        assert (await service.get_update_outcome())["announce_version"] == "1.2.3"

        service.dismiss_update_announcement()
        outcome = await service.get_update_outcome()
        assert (outcome["announce_version"], outcome["announce_direction"]) == (None, None)


class TestAStartOnAVersionNeitherSideOfWhichIsTheLater:
    async def test_announces_nothing_logs_nothing_and_still_records_the_version(self, logger, caplog):
        service, factory, _, _ = _make(logger, running="1.3.0", last_run="development")

        with caplog.at_level(logging.INFO, logger=logger.name):
            service.note_start()

        outcome = await service.get_update_outcome()
        assert (outcome["announce_version"], outcome["announce_direction"], outcome["toast_owed"]) == (
            None,
            None,
            False,
        )
        assert _lines(caplog, logger, logging.INFO) == []
        assert _last_run(factory) == "1.3.0"


class TestAStartOnTheSameVersion:
    async def test_announces_nothing(self, logger):
        service, factory, _, _ = _make(logger, running="1.3.0", last_run="1.3.0")

        service.note_start()

        assert (await service.get_update_outcome())["announce_version"] is None
        assert _last_run(factory) == "1.3.0"

    async def test_nothing_is_owed_before_the_start_was_noted(self, logger):
        service, _, _, _ = _make(logger, running="1.3.0", last_run="1.2.3")

        assert (await service.get_update_outcome())["announce_version"] is None


class TestTheFirstStart:
    async def test_records_the_version_and_announces_nothing(self, logger, caplog):
        service, factory, _, _ = _make(logger, running="1.3.0")

        with caplog.at_level(logging.INFO, logger=logger.name):
            service.note_start()

        assert _last_run(factory) == "1.3.0"
        assert (await service.get_update_outcome())["announce_version"] is None
        assert _lines(caplog, logger, logging.INFO) == []


class TestAStartAfterARollback:
    async def test_the_restored_database_s_version_announces_nothing(self, logger):
        """The installer put the database back, so the stored version is the restored one."""
        service, _, _, _ = _make(logger, running="1.2.3", last_run="1.2.3", record=_Record(_FAILURE))

        service.note_start()

        assert (await service.get_update_outcome())["announce_version"] is None

    async def test_a_stored_version_of_the_failed_update_announces_nothing_either(self, logger, caplog):
        service, factory, _, _ = _make(logger, running="1.2.3", last_run="1.3.0", record=_Record(_FAILURE))

        with caplog.at_level(logging.INFO, logger=logger.name):
            service.note_start()

        assert (await service.get_update_outcome())["announce_version"] is None
        assert _lines(caplog, logger, logging.INFO) == []
        assert _last_run(factory) == "1.2.3"

    def test_logs_one_warning_naming_both_versions_and_when(self, logger, caplog):
        service, _, _, _ = _make(logger, running="1.2.3", last_run="1.2.3", record=_Record(_FAILURE))

        with caplog.at_level(logging.INFO, logger=logger.name):
            service.note_start()

        assert _lines(caplog, logger, logging.WARNING) == [
            "the update to 1.3.0 was rolled back at 2026-09-25T10:15:00Z; back on 1.2.3"
            " — what 1.3.0 logged when it tried to start is earlier in this log,"
            " or in journalctl --user -u romm-tender if it failed before logging"
        ]

    def test_logs_it_even_when_the_card_was_dismissed(self, logger, caplog):
        """The log is where the reason is looked for, and a dismissed card does not make it untrue."""
        settings = {FAILURE_DISMISSED_KEY: _FAILURE.rolled_back_at}
        service, _, _, _ = _make(logger, running="1.2.3", last_run="1.2.3", record=_Record(_FAILURE), settings=settings)

        with caplog.at_level(logging.WARNING, logger=logger.name):
            service.note_start()

        assert len(_lines(caplog, logger, logging.WARNING)) == 1

    def test_a_refusal_by_the_check_is_logged_as_one_and_not_as_a_rollback(self, logger, caplog):
        service, _, _, _ = _make(logger, running="1.2.3", last_run="1.2.3", record=_Record(_REFUSED))

        with caplog.at_level(logging.INFO, logger=logger.name):
            service.note_start()

        assert _lines(caplog, logger, logging.WARNING) == [
            "the installer's check refused 1.3.0 at 2026-09-25T10:15:00Z: it did not start, so nothing was changed"
            " and this is still 1.2.3 — what it said is in journalctl --user -u romm-tender-update,"
            " or in the terminal the installer ran in"
        ]

    def test_no_record_logs_no_warning(self, logger, caplog):
        service, _, _, _ = _make(logger, running="1.2.3", last_run="1.2.3")

        with caplog.at_level(logging.WARNING, logger=logger.name):
            service.note_start()

        assert _lines(caplog, logger, logging.WARNING) == []


class TestAnAnnouncementBesideAStandingRecord:
    """Both can stand at once: the announcement names the running version, which a standing record says it restored."""

    @pytest.mark.parametrize(
        ("last_run", "direction"),
        [
            # The installer's removal did not happen, 1.3.1 went through, and the reader went back to 1.2.3.
            ("1.3.1", "back"),
            # The same leftover, and the reader came up to 1.2.3 from an earlier release.
            ("1.2.0", "updated"),
        ],
    )
    async def test_a_version_that_moved_onto_the_restored_one_is_announced_beside_the_record(
        self, logger, last_run, direction
    ):
        service, _, _, _ = _make(logger, running="1.2.3", last_run=last_run, record=_Record(_FAILURE))

        service.note_start()

        outcome = await service.get_update_outcome()
        assert (outcome["announce_version"], outcome["announce_direction"]) == ("1.2.3", direction)
        assert outcome["failure"] is not None
        assert outcome["failure"]["attempted_version"] == "1.3.0"


class TestARecordThatNoLongerStands:
    """A record stands only while the running version is the one it restored; any other is a leftover."""

    async def test_a_later_update_that_went_through_logs_no_rollback_and_reports_no_record(self, logger, caplog):
        """The installer's removal did not happen, and the update to 1.3.0 went through after all."""
        service, _, _, _ = _make(logger, running="1.3.0", last_run="1.2.3", record=_Record(_FAILURE))

        with caplog.at_level(logging.INFO, logger=logger.name):
            service.note_start()
            outcome = await service.get_update_outcome()

        assert _lines(caplog, logger, logging.WARNING) == []
        assert _lines(caplog, logger, logging.INFO) == ["updated from 1.2.3 to 1.3.0"]
        assert outcome == {
            "announce_version": "1.3.0",
            "announce_direction": "updated",
            "toast_owed": True,
            "failure": None,
            "failure_dismissed": False,
        }

    async def test_a_record_whose_restored_version_is_not_running_is_ignored(self, logger, caplog):
        service, _, _, _ = _make(logger, running="1.4.0", last_run="1.4.0", record=_Record(_FAILURE))

        with caplog.at_level(logging.WARNING, logger=logger.name):
            service.note_start()
            outcome = await service.get_update_outcome()

        assert _lines(caplog, logger, logging.WARNING) == []
        assert outcome["failure"] is None
        assert outcome["failure_dismissed"] is False


class TestTheRecord:
    async def test_is_reported_with_its_four_fields(self, logger):
        service, _, _, _ = _make(logger, running="1.2.3", record=_Record(_FAILURE))

        outcome = await service.get_update_outcome()

        assert set(outcome) == _OUTCOME_KEYS
        assert outcome["failure"] == {
            "attempted_version": "1.3.0",
            "restored_version": "1.2.3",
            "rolled_back_at": "2026-09-25T10:15:00Z",
            "kind": "rollback",
        }
        assert outcome["failure_dismissed"] is False

    async def test_a_refusal_by_the_check_is_reported_as_the_check_s(self, logger):
        service, _, _, _ = _make(logger, running="1.2.3", record=_Record(_REFUSED))

        outcome = await service.get_update_outcome()

        assert outcome["failure"] == {
            "attempted_version": "1.3.0",
            "restored_version": "1.2.3",
            "rolled_back_at": "2026-09-25T10:15:00Z",
            "kind": "check",
        }

    async def test_is_read_afresh_so_it_goes_when_the_installer_removes_it(self, logger):
        record = _Record(_FAILURE)
        service, _, _, _ = _make(logger, running="1.2.3", record=record)
        assert (await service.get_update_outcome())["failure"] is not None

        record.failure = None

        assert (await service.get_update_outcome()) == {
            "announce_version": None,
            "announce_direction": None,
            "toast_owed": False,
            "failure": None,
            "failure_dismissed": False,
        }

    async def test_a_raising_seam_is_no_record_and_reaches_the_log(self, logger, caplog):
        service, _, _, _ = _make(logger, running="1.2.3", record=_Record(raises=OSError("gone")))

        with caplog.at_level(logging.WARNING, logger=logger.name):
            service.note_start()
            outcome = await service.get_update_outcome()

        assert outcome["failure"] is None
        assert any("could not be read" in line for line in _lines(caplog, logger, logging.WARNING))


class TestDismissingTheCard:
    async def test_dismisses_that_record_and_persists(self, logger):
        service, _, settings, persister = _make(logger, running="1.2.3", record=_Record(_FAILURE))

        assert service.dismiss_update_failure(_FAILURE.rolled_back_at) == {"success": True}

        assert settings[FAILURE_DISMISSED_KEY] == "2026-09-25T10:15:00Z"
        assert persister.save_count == 1
        assert (await service.get_update_outcome())["failure_dismissed"] is True

    async def test_a_later_rollback_raises_the_card_again(self, logger):
        record = _Record(_FAILURE)
        service, _, _, _ = _make(logger, running="1.2.3", record=record)
        service.dismiss_update_failure(_FAILURE.rolled_back_at)

        record.failure = UpdateFailure(
            attempted_version="1.3.1", restored_version="1.2.3", rolled_back_at="2026-09-26T08:00:00Z"
        )

        assert (await service.get_update_outcome())["failure_dismissed"] is False

    async def test_a_dismissal_with_no_record_standing_dismisses_nothing(self, logger):
        service, _, _, _ = _make(logger, settings={FAILURE_DISMISSED_KEY: _FAILURE.rolled_back_at})

        assert (await service.get_update_outcome())["failure_dismissed"] is False

    @pytest.mark.parametrize("unusable", [None, "", 42, ["2026-09-25T10:15:00Z"]])
    def test_a_stamp_that_is_not_one_is_refused(self, logger, unusable):
        service, _, settings, persister = _make(logger)

        assert service.dismiss_update_failure(unusable) == {
            "success": False,
            "reason": "invalid_value",
            "message": "Invalid record",
        }
        assert FAILURE_DISMISSED_KEY not in settings
        assert persister.save_count == 0

    async def test_a_dismissal_stored_as_something_else_does_not_hide_the_card(self, logger):
        service, _, _, _ = _make(logger, running="1.2.3", record=_Record(_FAILURE), settings={FAILURE_DISMISSED_KEY: 7})

        outcome = await service.get_update_outcome()

        assert outcome["failure"] is not None
        assert outcome["failure_dismissed"] is False
