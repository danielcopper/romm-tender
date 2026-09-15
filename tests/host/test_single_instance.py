"""One backend at a time — the lock, the note it leaves, and the proof."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import textwrap
import time

import pytest

from host.single_instance import PortFile, SingleInstanceLock, someone_listening
from tests.host.conftest import free_port


class TestTheLock:
    def test_the_first_holder_gets_it(self, tmp_path):
        lock = SingleInstanceLock(str(tmp_path / "db" / "backend.lock"))
        try:
            assert lock.acquire() is True
        finally:
            lock.release()

    def test_it_creates_the_directory_it_lives_in(self, tmp_path):
        """It lies beside the database, which may not exist on a first start."""
        lock = SingleInstanceLock(str(tmp_path / "not-yet" / "backend.lock"))
        try:
            lock.acquire()
        finally:
            lock.release()

        assert (tmp_path / "not-yet" / "backend.lock").exists()

    def test_a_second_holder_is_refused(self, tmp_path):
        first = SingleInstanceLock(str(tmp_path / "backend.lock"))
        second = SingleInstanceLock(str(tmp_path / "backend.lock"), retry_seconds=0.0)
        first.acquire()
        try:
            assert second.acquire() is False
        finally:
            first.release()

    def test_a_second_process_is_refused(self, tmp_path):
        """The case that matters: two backends, not two objects."""
        path = tmp_path / "backend.lock"
        holder = subprocess.Popen(
            [sys.executable, "-c", _HOLD_THE_LOCK.format(path=str(path))],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "held"

            assert SingleInstanceLock(str(path), retry_seconds=0.0).acquire() is False
        finally:
            holder.terminate()
            holder.wait(timeout=10)

    def test_the_lock_dies_with_the_process_that_held_it(self, tmp_path):
        """``flock`` rather than a PID file: nothing stale survives a crash."""
        path = tmp_path / "backend.lock"
        holder = subprocess.Popen(
            [sys.executable, "-c", _HOLD_THE_LOCK.format(path=str(path))],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert holder.stdout is not None
        holder.stdout.readline()
        holder.kill()
        holder.wait(timeout=10)

        successor = SingleInstanceLock(str(path), retry_seconds=2.0)
        try:
            assert successor.acquire() is True
        finally:
            successor.release()

    def test_it_waits_for_a_predecessor_still_letting_go(self, tmp_path):
        """A service restart overlaps; without the window every restart would fail."""
        outgoing = SingleInstanceLock(str(tmp_path / "backend.lock"))
        outgoing.acquire()
        incoming = SingleInstanceLock(str(tmp_path / "backend.lock"), retry_seconds=0.6)

        started = time.monotonic()
        assert incoming.acquire() is False
        assert time.monotonic() - started >= 0.5

        outgoing.release()

    def test_the_lock_is_reusable_once_released(self, tmp_path):
        first = SingleInstanceLock(str(tmp_path / "backend.lock"))
        first.acquire()
        first.release()

        second = SingleInstanceLock(str(tmp_path / "backend.lock"), retry_seconds=0.0)
        try:
            assert second.acquire() is True
        finally:
            second.release()

    def test_releasing_twice_is_harmless(self, tmp_path):
        lock = SingleInstanceLock(str(tmp_path / "backend.lock"))
        lock.acquire()
        lock.release()

        lock.release()

    def test_releasing_one_never_taken_is_harmless(self, tmp_path):
        SingleInstanceLock(str(tmp_path / "backend.lock")).release()


_HOLD_THE_LOCK = textwrap.dedent(
    """
    import fcntl, os, sys, time
    fd = os.open({path!r}, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    print("held", flush=True)
    time.sleep(30)
    """
)


class TestThePortFile:
    def test_it_records_the_port(self, tmp_path):
        note = PortFile(str(tmp_path / "run" / "port"))
        note.write(27737)

        assert note.read() == 27737

    def test_it_creates_the_directory_it_lives_in(self, tmp_path):
        PortFile(str(tmp_path / "run" / "port")).write(27737)

        assert (tmp_path / "run" / "port").exists()

    def test_it_carries_no_pid(self, tmp_path):
        note = PortFile(str(tmp_path / "port"))
        note.write(27737)

        assert (tmp_path / "port").read_text() == "27737"

    def test_a_rewrite_replaces_the_earlier_note(self, tmp_path):
        note = PortFile(str(tmp_path / "port"))
        note.write(27737)
        note.write(27738)

        assert note.read() == 27738

    def test_no_temporary_file_is_left_behind(self, tmp_path):
        PortFile(str(tmp_path / "port")).write(27737)

        assert sorted(os.listdir(tmp_path)) == ["port"]

    def test_an_absent_note_reads_as_nothing(self, tmp_path):
        assert PortFile(str(tmp_path / "port")).read() is None

    @pytest.mark.parametrize("content", ["", "   ", "not a port", "27737 27738"])
    def test_an_unreadable_note_reads_as_nothing(self, tmp_path, content):
        (tmp_path / "port").write_text(content)

        assert PortFile(str(tmp_path / "port")).read() is None

    def test_removing_it_takes_it_away(self, tmp_path):
        note = PortFile(str(tmp_path / "port"))
        note.write(27737)
        note.remove()

        assert note.read() is None

    def test_removing_one_that_is_not_there_is_harmless(self, tmp_path):
        PortFile(str(tmp_path / "port")).remove()


class TestTheProof:
    def test_a_bound_port_answers(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)

            assert someone_listening(listener.getsockname()[1]) is True

    def test_an_unbound_port_does_not(self):
        """Why the note is a hint: a port in it may name nothing at all."""
        assert someone_listening(free_port(), timeout=0.2) is False
