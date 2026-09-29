"""Tests for adapters.transient_unit — starting a transient user unit, and asking whether it runs.

Every test runs against stub ``systemd-run`` / ``systemctl`` scripts on a
``PATH`` of their own: nothing here ever reaches this machine's user manager.
"""

from __future__ import annotations

import json
import stat

import pytest

from adapters.transient_unit import SystemdRunAdapter

_STUB = """#!/bin/sh
printf '%s\\n' "$0" "$@" > "{record}"
{body}
"""


@pytest.fixture
def stubs(tmp_path, monkeypatch):
    """A ``PATH`` holding only the stubs a test writes, and where each recorded its arguments."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))

    def write(name: str, body: str) -> str:
        record = tmp_path / f"{name}.args"
        script = bin_dir / name
        script.write_text(_STUB.format(record=record, body=body))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return str(record)

    return write


def _argv(record: str) -> list[str]:
    with open(record) as f:
        return f.read().splitlines()[1:]


class TestStart:
    def test_it_starts_the_command_as_a_collected_user_unit_with_the_environment_set(self, stubs):
        record = stubs("systemd-run", "exit 0")

        answer = SystemdRunAdapter().start(
            "romm-tender-update",
            ("/bin/bash", "/cache/update/install.sh", "--from", "/cache/update/t.tar.gz", "--yes"),
            (("TENDER_CODE_DIR", "/code dir/with space"), ("TENDER_PYTHON", "/usr/bin/python3.13")),
        )

        assert answer is None
        assert _argv(record) == [
            "--user",
            "--unit",
            "romm-tender-update",
            "--collect",
            "--quiet",
            "--setenv=TENDER_CODE_DIR=/code dir/with space",
            "--setenv=TENDER_PYTHON=/usr/bin/python3.13",
            "--",
            "/bin/bash",
            "/cache/update/install.sh",
            "--from",
            "/cache/update/t.tar.gz",
            "--yes",
        ]

    def test_a_refusal_answers_what_the_tool_said(self, stubs):
        said = "Failed to start transient service unit: Unit romm-tender-update.service was already loaded"
        stubs("systemd-run", f"echo '{said}' >&2; exit 1")

        answer = SystemdRunAdapter().start("romm-tender-update", ("/bin/true",), ())

        assert answer == said

    def test_a_silent_refusal_answers_its_status(self, stubs):
        stubs("systemd-run", "exit 3")

        assert SystemdRunAdapter().start("u", ("/bin/true",), ()) == "systemd-run exited with status 3"

    def test_no_systemd_run_is_an_answer_not_an_exception(self, stubs):
        assert SystemdRunAdapter().start("u", ("/bin/true",), ()) == "systemd-run is not installed"


class TestIsActive:
    @pytest.mark.parametrize("state", ["active", "activating", "deactivating", "reloading"])
    def test_a_unit_still_running_is_active(self, stubs, state):
        record = stubs("systemctl", f"echo {state}")

        assert SystemdRunAdapter().is_active("romm-tender-update") is True
        assert _argv(record) == ["--user", "show", "--property=ActiveState", "--value", "romm-tender-update"]

    @pytest.mark.parametrize("state", ["inactive", "failed"])
    def test_a_unit_that_ended_or_was_collected_is_not(self, stubs, state):
        stubs("systemctl", f"echo {state}")

        assert SystemdRunAdapter().is_active("romm-tender-update") is False

    @pytest.mark.parametrize("body", ["exit 1", "echo maintenance", "echo"])
    def test_an_answer_that_says_neither_is_none(self, stubs, body):
        stubs("systemctl", body)

        assert SystemdRunAdapter().is_active("romm-tender-update") is None

    def test_no_systemctl_is_none(self, stubs):
        assert SystemdRunAdapter().is_active("romm-tender-update") is None


def test_the_stubs_record_exactly_what_they_were_given(stubs):
    """Guards the argv reading above: a stub that recorded nothing would make every assertion vacuous."""
    record = stubs("systemd-run", "exit 0")

    SystemdRunAdapter().start("u", ("/bin/echo", json.dumps({"a": 1})), ())

    assert _argv(record)[-1] == '{"a": 1}'
