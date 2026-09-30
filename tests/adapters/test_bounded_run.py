"""Tests for adapters.bounded_run — a short command run to its end, every wait bounded."""

from __future__ import annotations

import io
import subprocess
import time

import pytest

from adapters import bounded_run
from adapters.bounded_run import run_bounded


class TestRun:
    def test_answers_what_the_command_printed_and_how_it_exited(self):
        done = run_bounded(["/bin/sh", "-c", "echo out; echo err >&2; exit 3"], timeout=5)

        assert (done.returncode, done.stdout, done.stderr) == (3, "out\n", "err\n")

    def test_bytes_that_are_not_utf_8_are_replaced_rather_than_raising(self):
        done = run_bounded(["/bin/sh", "-c", r"printf 'a\377b'"], timeout=5)

        assert done.stdout == "a�b"

    def test_a_command_that_cannot_be_started_raises_os_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            run_bounded([str(tmp_path / "missing")], timeout=5)

    def test_a_command_that_overruns_is_killed_and_raises_a_timeout(self):
        began = time.monotonic()

        with pytest.raises(subprocess.TimeoutExpired):
            run_bounded(["/bin/sleep", "5"], timeout=0.2)

        assert time.monotonic() - began < 3


class _Unkillable:
    """A process that neither ends in time nor goes on ``SIGKILL``, as one in uninterruptible sleep does."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.killed = False
        self.waits: list[float | None] = []

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        raise subprocess.TimeoutExpired("stub", timeout or 0)

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout)
        raise subprocess.TimeoutExpired("stub", timeout or 0)


class TestAfterATimeout:
    def test_the_wait_for_a_killed_command_is_bounded_and_its_pipes_are_closed(self, monkeypatch):
        spawned: list[_Unkillable] = []

        def popen(*args, **kwargs):
            spawned.append(_Unkillable(*args, **kwargs))
            return spawned[-1]

        monkeypatch.setattr(bounded_run.subprocess, "Popen", popen)

        with pytest.raises(subprocess.TimeoutExpired):
            run_bounded(["stub"], timeout=0.1)

        (process,) = spawned
        assert process.killed
        assert process.waits == [bounded_run._REAP_SECONDS]
        assert process.stdout.closed
        assert process.stderr.closed
