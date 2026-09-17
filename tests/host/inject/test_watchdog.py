"""The crash record, in every direction it can be read and written."""

from __future__ import annotations

import json
import os

import pytest

from host.inject.watchdog import (
    INJECT_ENV,
    INJECT_FORCE,
    WATCHDOG_FILENAME,
    CrashWatchdog,
    Fingerprint,
)

SAME = Fingerprint(tender="1.0.0", bundle="abc123", steam="1788652215")


@pytest.fixture
def record_path(tmp_path):
    return str(tmp_path / "state" / WATCHDOG_FILENAME)


def stored(path: str) -> dict[str, object]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class TestACleanRun:
    def test_the_first_ever_attempt_may_inject_and_says_nothing(self, record_path):
        verdict = CrashWatchdog(record_path).judge(SAME)
        assert verdict.may_inject
        assert verdict.failures == 0
        assert verdict.line == ""

    def test_an_attempt_that_survived_leaves_no_open_record(self, record_path):
        watchdog = CrashWatchdog(record_path)
        watchdog.judge(SAME)
        watchdog.arm(SAME)
        assert stored(record_path)["open"] is True
        watchdog.survived()
        assert stored(record_path)["open"] is False
        assert stored(record_path)["failures"] == 0

    def test_a_survived_attempt_is_not_counted_by_the_next_one(self, record_path):
        first = CrashWatchdog(record_path)
        first.judge(SAME)
        first.arm(SAME)
        first.survived()
        assert CrashWatchdog(record_path).judge(SAME).failures == 0


class TestAnOpenRecord:
    def test_a_record_left_open_counts_as_one_failure_at_the_next_attempt(self, record_path):
        armed = CrashWatchdog(record_path)
        armed.judge(SAME)
        armed.arm(SAME)

        verdict = CrashWatchdog(record_path).judge(SAME)
        assert verdict.may_inject
        assert verdict.failures == 1
        assert "1 of 2" in verdict.line

    def test_resolving_an_open_record_closes_it_so_it_cannot_count_twice(self, record_path):
        armed = CrashWatchdog(record_path)
        armed.judge(SAME)
        armed.arm(SAME)

        assert CrashWatchdog(record_path).judge(SAME).failures == 1
        assert CrashWatchdog(record_path).judge(SAME).failures == 1

    def test_two_in_a_row_stop_the_injection(self, record_path):
        for _ in range(2):
            watchdog = CrashWatchdog(record_path)
            assert watchdog.judge(SAME).may_inject
            watchdog.arm(SAME)

        verdict = CrashWatchdog(record_path).judge(SAME)
        assert not verdict.may_inject
        assert verdict.failures == 2

    def test_the_refusal_names_the_state_and_the_way_out(self, record_path):
        for _ in range(2):
            watchdog = CrashWatchdog(record_path)
            watchdog.judge(SAME)
            watchdog.arm(SAME)

        line = CrashWatchdog(record_path).judge(SAME).line
        assert SAME.tender in line
        assert SAME.steam in line
        assert f"{INJECT_ENV}={INJECT_FORCE}" in line

    def test_an_attempt_that_established_nothing_is_not_counted(self, record_path):
        watchdog = CrashWatchdog(record_path)
        watchdog.judge(SAME)
        watchdog.arm(SAME)
        watchdog.inconclusive()

        assert CrashWatchdog(record_path).judge(SAME).failures == 0

    def test_surviving_after_one_failure_puts_the_count_back_to_nothing(self, record_path):
        armed = CrashWatchdog(record_path)
        armed.judge(SAME)
        armed.arm(SAME)

        second = CrashWatchdog(record_path)
        assert second.judge(SAME).failures == 1
        second.arm(SAME)
        second.survived()

        assert CrashWatchdog(record_path).judge(SAME).failures == 0


class TestItStartsTryingAgainByItself:
    @pytest.mark.parametrize(
        ("moved", "named"),
        [
            (Fingerprint(tender="1.0.1", bundle=SAME.bundle, steam=SAME.steam), "Tender's version"),
            (Fingerprint(tender=SAME.tender, bundle="deadbeef", steam=SAME.steam), "the panel bundles"),
            (Fingerprint(tender=SAME.tender, bundle=SAME.bundle, steam="1799999999"), "Steam's build"),
        ],
    )
    def test_each_of_the_three_readings_drops_the_count(self, record_path, moved, named):
        for _ in range(2):
            watchdog = CrashWatchdog(record_path)
            watchdog.judge(SAME)
            watchdog.arm(SAME)
        assert not CrashWatchdog(record_path).judge(SAME).may_inject

        verdict = CrashWatchdog(record_path).judge(moved)
        assert verdict.may_inject
        assert verdict.failures == 0
        assert named in verdict.line

    def test_a_change_with_nothing_to_forgive_says_nothing(self, record_path):
        watchdog = CrashWatchdog(record_path)
        watchdog.judge(SAME)
        watchdog.arm(SAME)
        watchdog.survived()

        moved = Fingerprint(tender="2.0.0", bundle=SAME.bundle, steam=SAME.steam)
        assert CrashWatchdog(record_path).judge(moved).line == ""

    def test_an_unchanged_fingerprint_keeps_the_refusal(self, record_path):
        for _ in range(2):
            watchdog = CrashWatchdog(record_path)
            watchdog.judge(SAME)
            watchdog.arm(SAME)
        assert not CrashWatchdog(record_path).judge(SAME).may_inject


class TestTheSwitch:
    def test_force_injects_over_a_refusal_and_says_so(self, record_path):
        for _ in range(2):
            watchdog = CrashWatchdog(record_path)
            watchdog.judge(SAME)
            watchdog.arm(SAME)

        verdict = CrashWatchdog(record_path, override=INJECT_FORCE).judge(SAME)
        assert verdict.may_inject
        assert f"{INJECT_ENV}={INJECT_FORCE}" in verdict.line

    def test_force_says_nothing_when_there_was_nothing_to_override(self, record_path):
        assert CrashWatchdog(record_path, override=INJECT_FORCE).judge(SAME).line == ""


class TestWhatItDoesWithAnUnusableFile:
    def test_a_record_that_is_not_json_is_read_as_no_record(self, record_path):
        os.makedirs(os.path.dirname(record_path), exist_ok=True)
        with open(record_path, "w", encoding="utf-8") as handle:
            handle.write("{half written")
        assert CrashWatchdog(record_path).judge(SAME).failures == 0

    def test_a_record_whose_count_is_not_a_number_is_read_as_no_count(self, record_path):
        os.makedirs(os.path.dirname(record_path), exist_ok=True)
        with open(record_path, "w", encoding="utf-8") as handle:
            json.dump({"failures": "lots", "open": True}, handle)
        assert CrashWatchdog(record_path).judge(SAME).failures == 1

    def test_a_record_with_half_a_fingerprint_is_read_as_none(self, record_path):
        os.makedirs(os.path.dirname(record_path), exist_ok=True)
        with open(record_path, "w", encoding="utf-8") as handle:
            json.dump({"fingerprint": {"tender": "1.0.0"}, "failures": 2, "open": False}, handle)
        verdict = CrashWatchdog(record_path).judge(SAME)
        assert not verdict.may_inject
        assert verdict.line != ""

    def test_a_directory_it_cannot_write_to_does_not_stop_the_injection(self, tmp_path):
        unwritable = tmp_path / "read-only"
        unwritable.mkdir(mode=0o500)
        try:
            watchdog = CrashWatchdog(str(unwritable / "sub" / WATCHDOG_FILENAME))
            assert watchdog.judge(SAME).may_inject
            watchdog.arm(SAME)
            watchdog.survived()
        finally:
            unwritable.chmod(0o700)


class TestWhoAnswersForAnArmedRecord:
    """Exactly one of three answers follows an ``arm``, and a shutdown reads which."""

    def test_arming_leaves_a_record_nobody_has_answered_for(self, record_path):
        watchdog = CrashWatchdog(record_path)
        watchdog.judge(SAME)
        assert watchdog.armed is False
        watchdog.arm(SAME)
        assert watchdog.armed is True

    def test_each_of_the_three_answers_settles_it(self, record_path):
        for answer in ("survived", "inconclusive", "stays_open"):
            watchdog = CrashWatchdog(record_path)
            watchdog.judge(SAME)
            watchdog.arm(SAME)
            getattr(watchdog, answer)()
            assert watchdog.armed is False, answer

    def test_leaving_it_open_writes_nothing_and_keeps_the_record_open(self, record_path):
        watchdog = CrashWatchdog(record_path)
        watchdog.judge(SAME)
        watchdog.arm(SAME)
        watchdog.stays_open()

        assert stored(record_path)["open"] is True
        assert CrashWatchdog(record_path).judge(SAME).failures == 1
