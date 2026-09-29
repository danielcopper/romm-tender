"""Tests for domain.update_install — the names and the vocabulary of an install from the panel."""

from __future__ import annotations

import pytest

from domain.app_directories import AppDirectories
from domain.update_install import (
    InstallAttempt,
    InstallFailure,
    InstallStep,
    Wait,
    WaitReason,
    installer_command,
    installer_environment,
)

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
