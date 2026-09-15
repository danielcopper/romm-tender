"""Bringing the backend up, in the one order that makes its signals mean anything.

Contract: the start-up sequence of the hosting process, and its shutdown. The
order below is the whole point of this module::

    take the lock (retrying briefly)
      -> schema migration and the start-up routines
      -> bind the port (preferred, then falling back)
      -> write the port file
      -> the one start-up step that talks to the network

Because the port is bound only after the schema and the start-up routines are
through, **"the port file is there" simply means "the backend is ready"**. A
process that dies at the schema migration has never announced a port, so nothing
has to wait for a readiness signal and no call has to be held pending one. The
draft's waiting mechanism — listen at once, queue calls until a ready flag —
disappears with it.

The credential migration comes last because it is the only start-up step that
makes a network request. Ahead of the bind it would hold readiness hostage to a
server that may be unreachable; behind it, a slow or failing RomM costs the
panel nothing.

**What is fatal and what is not is decided here, and the two are not the same
question.** Without the lock, the schema, the wiring or a port there is no
backend, so those end the process. The start-up routines are repairs: six of the
nine contain no exception handling at all, and under the plugin loader that was
harmless because the lifecycle hook was a detached task. Hosted, an unhandled
failure in a cover-cache sweep would take the whole backend down — and with a
service manager's restart policy, do it again on every start. So each routine
runs inside a reporting wrapper, and a failure is counted rather than fatal.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING

from host.server import DEFAULT_PORT, HostServer
from host.single_instance import PortFile, SingleInstanceLock, someone_listening

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable

    from host.dispatch import CallDispatcher
    from host.events import EventSink
    from host.status import HostStatus


@dataclass(frozen=True)
class BackendBuild:
    """What building the backend produced that the host itself needs.

    The identity comes from here rather than from the caller because it is read
    off the package manifest during the build, and reading that file a second
    time in the entry point would be a second spelling of the program's name,
    free to drift from the one every outgoing request already carries.
    """

    dispatcher: CallDispatcher
    server_identity: str


class AlreadyRunningError(RuntimeError):
    """Raised when another backend holds the lock.

    Carries where that one listens when it could be established, because a
    person who started a second copy wants to be told where the first one is —
    not that a file exists.
    """


async def run_backend(
    *,
    build: Callable[[], Awaitable[BackendBuild]],
    after_bind: Callable[[], Awaitable[None]],
    shutdown: Callable[[], Awaitable[None]],
    events: EventSink,
    status: HostStatus,
    static_root: str,
    lock_path: str,
    port_file_path: str,
    logger: logging.Logger,
    token: str,
    preferred_port: int = DEFAULT_PORT,
) -> None:
    """Start the backend, serve until a termination signal, then shut it down.

    *build* performs the schema migration, the wiring and the start-up routines
    and answers with the dispatcher for the object calls reach, plus the identity
    this server answers under. *token* is this process's admission token, created
    by the caller because the logging filter that keeps it out of the log file
    has to exist before the first line is written. *after_bind* is
    the network-touching start-up step, run once the port has been announced.
    *shutdown* is awaited before the process ends — an interrupted unload would
    leave the very state the start-up routines exist to repair. *preferred_port*
    is the port asked for first; the bind falls back past a port some other
    program holds.

    Raises :class:`AlreadyRunningError` when another backend holds the lock.
    """
    lock = SingleInstanceLock(lock_path)
    port_file = PortFile(port_file_path)
    if not lock.acquire():
        raise AlreadyRunningError(_where_the_running_one_is(port_file))

    # Before the first start-up step, not after the last: a termination signal
    # arriving during the schema migration or the credential fetch would
    # otherwise take the default action and kill the process outright, leaving
    # behind exactly the half-finished state the start-up routines exist to
    # repair.
    stop = _listen_for_termination()
    server: HostServer | None = None
    try:
        built = await build()

        server = HostServer(
            dispatcher=built.dispatcher,
            events=events,
            static_root=static_root,
            logger=logger,
            server_identity=built.server_identity,
            token=token,
            preferred_port=preferred_port,
        )
        status.port = await server.start()
        status.count_dropped_messages = lambda: server.dropped_messages
        port_file.write(status.port)

        # Once, at start-up, with the token in it: this is the address the panel
        # is loaded from, and without it the development loop cannot be driven
        # by hand at all. The log file never sees it — that handler's own
        # formatter redacts the token — so this line lives on stderr, which is
        # the terminal for a hand start and the journal for a service.
        logger.info(f"host: load the panel from {server.bundle_url()}")
        if status.failed_startup_steps:
            logger.warning(f"host: {len(status.failed_startup_steps)} start-up step(s) failed; the panel will say so")

        await after_bind()
        await stop.wait()
        logger.info("host: termination signal received")
    finally:
        _stop_listening_for_termination()
        logger.info("host: shutting down")
        with contextlib.suppress(Exception):
            await shutdown()
        if server is not None:
            await server.stop()
        port_file.remove()
        lock.release()


_TERMINATION_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def _listen_for_termination() -> asyncio.Event:
    """Arrange for SIGTERM and SIGINT to set an event, and return it.

    The unload runs after the wait returns rather than inside the handler,
    because unloading is asynchronous and a signal handler is not a place to
    await anything.
    """
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for received in _TERMINATION_SIGNALS:
        with contextlib.suppress(NotImplementedError, ValueError):
            loop.add_signal_handler(received, stop.set)
    return stop


def _stop_listening_for_termination() -> None:
    """Hand both signals back to their default disposition."""
    loop = asyncio.get_running_loop()
    for received in _TERMINATION_SIGNALS:
        with contextlib.suppress(NotImplementedError, ValueError, RuntimeError):
            loop.remove_signal_handler(received)


def _where_the_running_one_is(port_file: PortFile) -> str:
    """Describe where the backend already running can be reached.

    The file is a hint and connecting is the proof, so both are reported: a note
    nobody answers on is a leftover from a crash, and saying so is more use than
    naming a port that leads nowhere.
    """
    port = port_file.read()
    if port is None:
        return "another backend holds the lock, and left no port to find it by"
    if someone_listening(port):
        return f"another backend is already running on 127.0.0.1:{port}"
    return f"another backend holds the lock; its note names port {port}, but nothing answers there"
