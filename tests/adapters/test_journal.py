"""Tests for adapters.journal — reading this user's journal through ``journalctl``.

Every test runs against a stub ``journalctl`` on a ``PATH`` of its own, printing
synthetic lines: nothing here ever reads this machine's journal.
"""

from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING

import pytest

from adapters.journal import JournalctlAdapter
from domain.update_output import JournalEntry

if TYPE_CHECKING:
    from collections.abc import Sequence

_STUB = """#!/bin/sh
printf '%s\\n' "$@" > "{record}"
{body}
"""

_LINES = [
    {"__REALTIME_TIMESTAMP": "1790794693000000", "_SYSTEMD_INVOCATION_ID": "aa11", "MESSAGE": "[..] Checking"},
    {"__REALTIME_TIMESTAMP": "1790794694500000", "USER_INVOCATION_ID": "aa11", "MESSAGE": "Failed with result"},
]


@pytest.fixture
def journalctl(tmp_path, monkeypatch):
    """Write the stub ``journalctl`` with *body*; answer the file it records its arguments in."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))

    def write(body: str) -> str:
        record = tmp_path / "journalctl.args"
        script = bin_dir / "journalctl"
        script.write_text(_STUB.format(record=record, body=body))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return str(record)

    return write


def _printing(lines: Sequence[object]) -> str:
    """A stub body printing *lines* with the shell's own ``printf``: the stub's ``PATH`` holds nothing else."""
    return "\n".join(f"printf '%s\\n' '{json.dumps(line)}'" for line in lines)


def _adapter(said: list[str] | None = None) -> JournalctlAdapter:
    return JournalctlAdapter(log_debug=(said if said is not None else []).append)


def _argv(record: str) -> list[str]:
    with open(record) as f:
        return f.read().splitlines()


class TestRead:
    def test_reads_one_unit_s_entries_between_two_instants_oldest_first(self, journalctl):
        record = journalctl(_printing(_LINES))

        entries = _adapter()("romm-tender-update", since=1790794600, until=1790794700.5)

        assert entries == (
            JournalEntry(at=1790794693.0, invocation="aa11", message="[..] Checking"),
            JournalEntry(at=1790794694.5, invocation="aa11", message="Failed with result"),
        )
        assert _argv(record) == [
            "--user",
            "--output=json",
            "--output-fields=MESSAGE,_SYSTEMD_INVOCATION_ID,USER_INVOCATION_ID",
            "--all",
            "--quiet",
            "--no-pager",
            "--unit=romm-tender-update",
            "--since=@1790794600.000000",
            "--until=@1790794700.500000",
        ]

    def test_reads_every_unit_and_only_the_newest_entries_when_asked(self, journalctl):
        record = journalctl(_printing(_LINES[-1:]))

        entries = _adapter()(None, until=1790794700, last=1)

        assert len(entries) == 1
        args = _argv(record)
        assert not any(arg.startswith("--unit") for arg in args)
        assert args[-2:] == ["--until=@1790794700.000000", "--lines=1"]

    def test_a_journal_with_no_such_entry_answers_nothing(self, journalctl):
        journalctl("exit 0")

        assert _adapter()("romm-tender-update") == ()

    def test_a_line_it_cannot_read_is_skipped_and_the_rest_are_kept(self, journalctl):
        journalctl("echo 'not json'\n" + _printing(_LINES[:1]))

        assert len(_adapter()("romm-tender-update")) == 1

    def test_what_a_journalctl_that_succeeded_said_on_stderr_is_logged_at_debug(self, journalctl):
        journalctl("echo 'Journal file x.journal~ is truncated, ignoring file.' >&2\n" + _printing(_LINES[:1]))
        said: list[str] = []

        assert len(_adapter(said)("romm-tender-update")) == 1
        assert said == ["[update] journalctl said: Journal file x.journal~ is truncated, ignoring file."]

    def test_a_journalctl_that_succeeded_quietly_logs_nothing(self, journalctl):
        journalctl(_printing(_LINES[:1]))
        said: list[str] = []

        _adapter(said)("romm-tender-update")

        assert said == []

    def test_a_journalctl_that_failed_raises_with_what_it_said(self, journalctl):
        journalctl("echo 'Failed to open journal' >&2; exit 1")
        adapter = _adapter()

        with pytest.raises(OSError, match="status 1: Failed to open journal"):
            adapter("romm-tender-update")

    def test_a_missing_journalctl_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        adapter = _adapter()

        with pytest.raises(OSError):
            adapter("romm-tender-update")

    def test_a_journalctl_that_gives_no_answer_in_time_raises_a_timeout(self, journalctl, monkeypatch):
        monkeypatch.setattr("adapters.journal._TIMEOUT_SECONDS", 0.2)
        journalctl("exec /bin/sleep 5")
        adapter = _adapter()

        with pytest.raises(TimeoutError):
            adapter("romm-tender-update")
