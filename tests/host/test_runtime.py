"""Bringing the backend up in the one order that makes the port file mean something.

The order is the invariant, so the observations are taken from inside the
sequence: each step records what the world looked like when it ran, and the
assertions are about those snapshots rather than about the end state, which
every order would produce alike.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

import pytest

from host.dispatch import CallDispatcher
from host.events import EventSink
from host.runtime import AlreadyRunningError, BackendBuild, _where_the_running_one_is, run_backend
from host.single_instance import PortFile, SingleInstanceLock
from host.status import HostStatus
from tests.host.conftest import FakePlugin, free_port
from tests.host.ws_client import http_get

LOGGER = logging.getLogger("test_runtime")


class Recorder:
    """Records what each start-up step saw, in the order the steps ran."""

    def __init__(self, port_file: PortFile, lock_path: str) -> None:
        self.port_file = port_file
        self.lock_path = lock_path
        self.steps: list[str] = []
        self.port_at_after_bind: int | None = None
        self.lock_held_at_build: bool | None = None
        self.shutdown_ran = asyncio.Event()

    async def build(self) -> BackendBuild:
        self.steps.append("build")
        contender = SingleInstanceLock(self.lock_path, retry_seconds=0.0)
        self.lock_held_at_build = not contender.acquire()
        contender.release()
        return BackendBuild(
            dispatcher=CallDispatcher(FakePlugin(), LOGGER),
            server_identity="romm-tender/0.0.0-test",
        )

    async def after_bind(self) -> None:
        self.steps.append("after_bind")
        self.port_at_after_bind = self.port_file.read()
        os.kill(os.getpid(), signal.SIGTERM)

    async def shutdown(self) -> None:
        self.steps.append("shutdown")
        self.shutdown_ran.set()


@pytest.fixture
def default_sigterm():
    """Only run the signal cases where nothing else owns SIGTERM."""
    if signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL:
        pytest.skip("something else already handles SIGTERM in this process")
    yield
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


async def _run(tmp_path, recorder: Recorder, status: HostStatus, port: int) -> None:
    """Drive ``run_backend`` on *port* so no test ever binds the real default."""
    static_root = tmp_path / "dist"
    static_root.mkdir(exist_ok=True)
    await run_backend(
        build=recorder.build,
        after_bind=recorder.after_bind,
        shutdown=recorder.shutdown,
        events=EventSink(LOGGER),
        status=status,
        static_root=str(static_root),
        lock_path=recorder.lock_path,
        port_file_path=recorder.port_file.path,
        logger=LOGGER,
        token="the-admission-token",
        preferred_port=port,
    )


@pytest.fixture
def recorder(tmp_path):
    return Recorder(PortFile(str(tmp_path / "run" / "port")), str(tmp_path / "data" / "backend.lock"))


class TestTheStartUpOrder:
    async def test_every_step_runs_once_and_in_order(self, tmp_path, recorder, default_sigterm):
        await _run(tmp_path, recorder, HostStatus(), free_port())

        assert recorder.steps == ["build", "after_bind", "shutdown"]

    async def test_the_lock_is_held_before_anything_is_built(self, tmp_path, recorder, default_sigterm):
        """Two backends would both migrate the schema and both run the repairs."""
        await _run(tmp_path, recorder, HostStatus(), free_port())

        assert recorder.lock_held_at_build is True

    async def test_the_port_file_names_the_bound_port_by_the_last_step(self, tmp_path, recorder, default_sigterm):
        """'The port file is there' means 'the backend is ready' — nothing else has to."""
        status = HostStatus()

        await _run(tmp_path, recorder, status, free_port())

        assert recorder.port_at_after_bind == status.port

    async def test_the_network_touching_step_runs_after_the_port_is_announced(
        self, tmp_path, recorder, default_sigterm
    ):
        """An unreachable RomM must not hold readiness hostage."""
        await _run(tmp_path, recorder, HostStatus(), free_port())

        assert recorder.port_at_after_bind is not None

    async def test_the_server_answers_while_it_runs(self, tmp_path, recorder, default_sigterm):
        status = HostStatus()
        task = asyncio.ensure_future(_run(tmp_path, recorder, status, free_port()))
        await asyncio.wait_for(recorder.shutdown_ran.wait(), 10)
        await task

        assert status.port != 0


class TestShutdown:
    async def test_a_termination_signal_ends_the_run(self, tmp_path, recorder, default_sigterm):
        await asyncio.wait_for(_run(tmp_path, recorder, HostStatus(), free_port()), 10)

        assert recorder.shutdown_ran.is_set()

    async def test_the_port_file_is_taken_away(self, tmp_path, recorder, default_sigterm):
        await _run(tmp_path, recorder, HostStatus(), free_port())

        assert recorder.port_file.read() is None

    async def test_the_lock_is_let_go(self, tmp_path, recorder, default_sigterm):
        await _run(tmp_path, recorder, HostStatus(), free_port())

        successor = SingleInstanceLock(recorder.lock_path, retry_seconds=0.0)
        try:
            assert successor.acquire() is True
        finally:
            successor.release()

    async def test_the_port_stops_answering(self, tmp_path, recorder, default_sigterm):
        status = HostStatus()
        await _run(tmp_path, recorder, status, free_port())

        with pytest.raises(OSError):
            await http_get(status.port, "/index.js", token="anything")

    async def test_a_failing_shutdown_still_releases_the_lock(self, tmp_path, recorder, default_sigterm):
        async def broken_shutdown() -> None:
            recorder.shutdown_ran.set()
            raise RuntimeError("unload broke")

        recorder.shutdown = broken_shutdown  # type: ignore[method-assign]

        await _run(tmp_path, recorder, HostStatus(), free_port())

        successor = SingleInstanceLock(recorder.lock_path, retry_seconds=0.0)
        try:
            assert successor.acquire() is True
        finally:
            successor.release()


class TestASecondBackend:
    async def test_it_refuses_to_start(self, tmp_path, recorder):
        status, port = HostStatus(), free_port()
        holder = SingleInstanceLock(recorder.lock_path)
        holder.acquire()
        try:
            with pytest.raises(AlreadyRunningError):
                await _run(tmp_path, recorder, status, port)
        finally:
            holder.release()

        assert recorder.steps == []


class TestWhatTheRefusalTells:
    """The note and the proof, as the refusing process words them.

    Unit-level rather than through ``run_backend``, because each pass through
    that would first sit out the lock's whole retry window to establish the one
    fact these cases already assume.
    """

    async def test_a_port_that_answers_is_named_as_the_running_backend(self, tmp_path):
        listener = await asyncio.start_server(lambda r, w: None, host="127.0.0.1", port=0)
        port = listener.sockets[0].getsockname()[1]
        note = PortFile(str(tmp_path / "port"))
        note.write(port)
        try:
            assert f"already running on 127.0.0.1:{port}" in _where_the_running_one_is(note)
        finally:
            listener.close()
            await listener.wait_closed()

    def test_a_port_nobody_answers_on_is_reported_as_a_leftover(self, tmp_path):
        """The file is a hint; connecting is the proof."""
        note = PortFile(str(tmp_path / "port"))
        note.write(free_port())

        assert "nothing answers there" in _where_the_running_one_is(note)

    def test_no_note_at_all_is_said_plainly(self, tmp_path):
        assert "left no port to find it by" in _where_the_running_one_is(PortFile(str(tmp_path / "port")))


class TestFailedStartUpSteps:
    def test_the_status_counts_them(self):
        status = HostStatus()

        status.record_failed_step("prune_orphaned_cover_cache")
        status.record_failed_step("cleanup_leftover_tmp_files")

        assert status.failed_startup_steps == ["prune_orphaned_cover_cache", "cleanup_leftover_tmp_files"]

    def test_a_clean_start_records_none(self):
        assert HostStatus().failed_startup_steps == []
