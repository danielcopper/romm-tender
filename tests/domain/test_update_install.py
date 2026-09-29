"""Tests for domain.update_install — the names and the vocabulary of an install from the panel."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain.app_directories import AppDirectories
from domain.update_install import (
    READ_ONLY_CLAIMS,
    InstallAttempt,
    InstallFailure,
    InstallStep,
    UpdateAttemptRecord,
    Wait,
    WaitReason,
    claim_reasons,
    claims_named_by,
    decode_attempt_record,
    encode_attempt_record,
    installer_command,
    installer_environment,
    new_attempt_record,
    stopped_attempt,
)
from domain.update_outcome import UpdateFailure

_DIRECTORIES = AppDirectories(
    config_dir="/home/u/.config/romm-tender",
    data_dir="/home/u/.local/share/romm-tender",
    cache_dir="/home/u/.cache/romm-tender",
    state_dir="/home/u/.local/state/romm-tender",
    runtime_dir="/run/user/1000/romm-tender",
    code_dir="/home/u/.local/lib/romm-tender",
    bin_dir="/home/u/.local/bin",
)
_DIRECTORY_PAIRS = (
    ("TENDER_CODE_DIR", "/home/u/.local/lib/romm-tender"),
    ("TENDER_CONFIG_DIR", "/home/u/.config/romm-tender"),
    ("TENDER_DATA_DIR", "/home/u/.local/share/romm-tender"),
    ("TENDER_CACHE_DIR", "/home/u/.cache/romm-tender"),
    ("TENDER_STATE_DIR", "/home/u/.local/state/romm-tender"),
    ("TENDER_BIN_DIR", "/home/u/.local/bin"),
    ("TENDER_PYTHON", "/usr/bin/python3.13"),
)


class TestInstallerEnvironment:
    def test_the_six_directories_as_resolved_and_the_interpreter(self):
        """The runtime directory is not one of them: the installer resolves its own from the session."""
        assert installer_environment({}, _DIRECTORIES, "/usr/bin/python3.13") == _DIRECTORY_PAIRS

    def test_the_directories_come_from_what_was_resolved_not_from_the_environment(self):
        environ = {"TENDER_DATA_DIR": "/elsewhere", "XDG_DATA_HOME": "/xdg"}

        assert installer_environment(environ, _DIRECTORIES, "/usr/bin/python3.13") == _DIRECTORY_PAIRS

    def test_each_pass_through_variable_this_process_has_is_handed_on(self):
        environ = {
            "XDG_CONFIG_HOME": "/home/u/cfg",
            "TENDER_RELEASE_API": "http://127.0.0.1:8000/latest",
            "TENDER_DOWNLOAD_BASE": "http://127.0.0.1:8000/download",
            "TENDER_UPDATE_WAIT": "5",
            "HOME": "/home/u",
        }

        pairs = installer_environment(environ, _DIRECTORIES, "/usr/bin/python3.13")

        assert pairs == (
            *_DIRECTORY_PAIRS,
            ("XDG_CONFIG_HOME", "/home/u/cfg"),
            ("TENDER_RELEASE_API", "http://127.0.0.1:8000/latest"),
            ("TENDER_DOWNLOAD_BASE", "http://127.0.0.1:8000/download"),
            ("TENDER_UPDATE_WAIT", "5"),
        )

    def test_an_empty_pass_through_variable_is_not_handed_on(self):
        assert installer_environment({"XDG_CONFIG_HOME": ""}, _DIRECTORIES, "/py") == (
            *_DIRECTORY_PAIRS[:-1],
            ("TENDER_PYTHON", "/py"),
        )


def test_the_installer_is_an_update_from_the_local_tarball_asking_nothing():
    assert installer_command("/c/update/install.sh", "/c/update/romm-tender-1.1.0.tar.gz") == (
        "/bin/bash",
        "/c/update/install.sh",
        "--from",
        "/c/update/romm-tender-1.1.0.tar.gz",
        "--yes",
    )


class TestWaitOnTheWire:
    def test_a_running_app_carries_its_names(self):
        assert Wait(WaitReason.APP_RUNNING, apps=("A", "B")).to_wire() == {"reason": "app_running", "apps": ["A", "B"]}

    def test_the_reload_limit_carries_when_it_frees_up(self):
        assert Wait(WaitReason.INTERFACE_RELOAD_LIMIT, frees_at=12.5).to_wire() == {
            "reason": "interface_reload_limit",
            "frees_at": 12.5,
        }

    @pytest.mark.parametrize(
        "reason", [r for r in WaitReason if r not in (WaitReason.APP_RUNNING, WaitReason.INTERFACE_RELOAD_LIMIT)]
    )
    def test_every_other_reason_is_its_name_alone(self, reason):
        assert Wait(reason, apps=("ignored",), frees_at=1.0).to_wire() == {"reason": reason.value}


class TestAttemptOnTheWire:
    def test_a_download_in_progress(self):
        attempt = InstallAttempt(version="1.1.0", step=InstallStep.DOWNLOADING, bytes_done=10, bytes_total=100)

        assert attempt.to_wire() == {
            "version": "1.1.0",
            "step": "downloading",
            "bytes_done": 10,
            "bytes_total": 100,
            "failure": None,
        }

    def test_a_failure_names_how(self):
        attempt = InstallAttempt(version="1.1.0", step=InstallStep.FAILED, failure=InstallFailure.CHECKSUM_MISMATCH)

        assert attempt.to_wire()["failure"] == "checksum_mismatch"


class TestClaimReasons:
    def test_a_claim_counts_toward_the_reason_that_names_its_work(self):
        assert claim_reasons(["sync_rom_saves", "start_download"]) == {WaitReason.SAVE_SYNC, WaitReason.ROM_DOWNLOADS}

    def test_work_of_another_kind_is_other_work(self):
        assert claim_reasons(["uninstall_all_roms", "launch_reconfirm"]) == {WaitReason.OTHER_WORK}

    def test_no_claim_is_no_reason(self):
        assert claim_reasons([]) == frozenset()

    def test_a_claim_no_entry_names_still_counts_as_other_work(self):
        """The safe side: a claim added without being classified makes a press wait rather than letting it through."""
        assert claim_reasons(["a_claim_nobody_classified"]) == {WaitReason.OTHER_WORK}

    def test_a_read_only_claim_is_no_reason(self):
        assert claim_reasons(["get_save_status", "test_connection"]) == frozenset()

    def test_a_read_only_claim_beside_work_leaves_the_work_s_reason(self):
        assert claim_reasons(["fetch_cover_base64", "sync_rom_saves"]) == {WaitReason.SAVE_SYNC}

    def test_no_claim_is_both_read_only_and_named_by_a_reason(self):
        named = set().union(*(claims_named_by(reason) for reason in WaitReason))

        assert named & READ_ONLY_CLAIMS == set()


_ATTEMPT = UpdateAttemptRecord(attempted_version="1.1.0", from_version="1.0.0", started_at="2026-09-29T10:00:00Z")


class TestTheAttemptRecord:
    def test_a_new_record_stamps_utc_to_the_second(self):
        now = datetime(2026, 9, 29, 10, 0, 0, 750000, tzinfo=UTC).timestamp()

        assert new_attempt_record("1.1.0", "1.0.0", now) == _ATTEMPT

    def test_it_reads_back_what_it_writes(self):
        assert decode_attempt_record(encode_attempt_record(_ATTEMPT)) == _ATTEMPT

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "{",
            "[]",
            '{"attempted_version": "1.1.0", "from_version": "1.0.0"}',
            '{"attempted_version": " ", "from_version": "1.0.0", "started_at": "x"}',
            '{"attempted_version": 1, "from_version": "1.0.0", "started_at": "x"}',
        ],
    )
    def test_anything_short_of_three_non_empty_strings_is_no_record(self, raw):
        assert decode_attempt_record(raw) is None


class TestAStoppedAttempt:
    def test_a_start_on_the_version_that_began_it_is_an_installer_that_stopped(self):
        assert stopped_attempt(_ATTEMPT, "1.0.0", None) == _ATTEMPT

    def test_a_start_on_the_attempted_version_is_an_update_that_went_through(self):
        assert stopped_attempt(_ATTEMPT, "1.1.0", None) is None

    def test_a_rollback_of_that_attempt_is_the_installer_s_record_to_tell(self):
        failure = UpdateFailure(attempted_version="1.1.0", restored_version="1.0.0", rolled_back_at="2026-09-29T10:01Z")

        assert stopped_attempt(_ATTEMPT, "1.0.0", failure) is None

    def test_a_rollback_of_another_attempt_does_not_explain_this_one(self):
        failure = UpdateFailure(attempted_version="1.0.5", restored_version="1.0.0", rolled_back_at="2026-09-01T10:01Z")

        assert stopped_attempt(_ATTEMPT, "1.0.0", failure) == _ATTEMPT

    def test_a_version_that_moved_some_other_way_since_is_over(self):
        assert stopped_attempt(_ATTEMPT, "0.9.0", None) is None

    def test_no_record_is_nothing(self):
        assert stopped_attempt(None, "1.0.0", None) is None
