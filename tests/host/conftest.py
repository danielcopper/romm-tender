"""Fixtures for the host tier — a real server on a real port.

Nothing here fakes the transport. The fixture starts a :class:`host.server.HostServer`
on a free loopback port with a small stand-in for the plugin object, and the
tests reach it through :mod:`tests.host._client`, which opens a TCP connection.
What is faked is only what the host is not: the object calls land on.

The served root is a ``tmp_path`` directory rather than the repository's real
``dist/``. That directory is git-ignored and is produced by a build pytest does
not run, so a suite pointed at it would pass or fail depending on whether
somebody had run the bundler.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import TYPE_CHECKING, Any

import pytest

from host.dispatch import CallDispatcher
from host.events import EventSink
from host.server import HostServer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

SERVER_IDENTITY = "romm-tender/0.0.0-test"


class FakePlugin:
    """Stands in for the plugin object: one method per shape a call can take."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def echo(self, value: Any) -> dict[str, Any]:
        self.calls.append(("echo", (value,)))
        return {"echo": value}

    async def no_arguments(self) -> str:
        self.calls.append(("no_arguments", ()))
        return "answered"

    async def boom(self) -> None:
        raise ValueError("the backend broke")

    async def blob(self, size: int) -> dict[str, str]:
        return {"blob": "x" * size}

    async def unserialisable(self) -> object:
        return object()

    async def never_returns(self) -> None:
        """Blocks until cancelled — how a call is caught in flight."""
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def _private(self) -> None:
        """Underscored, so no caller may reach it."""

    def synchronous(self) -> None:
        """Not a coroutine function, so no caller may reach it."""


def free_port() -> int:
    """A loopback port nothing is listening on right now."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def squat_run(count: int) -> tuple[int, list[asyncio.Server]]:
    """Hold *count* consecutive loopback ports, with the one after them free.

    ``free_port`` speaks for one port and says nothing about its neighbours, and
    the ephemeral range it draws from is exactly where other programs are handed
    theirs — so a test that assumed ``first + 1`` was free failed whenever the
    machine happened to be using it. This binds the whole run at once, which is
    what proves every port in it was free, and releases only the last so the
    server under test has somewhere to land.

    Returns the first port and the servers holding the run; the caller closes them.
    """
    for _ in range(64):
        held: list[asyncio.Server] = []
        first = free_port()
        try:
            # Appended one at a time on purpose: a bind that fails part-way through
            # the run must leave the ports already held in ``held``, so the except
            # below can give them back. ``list.extend`` over a comprehension would
            # discard them and leak a listener for the rest of the session.
            for offset in range(count + 1):
                held.append(  # noqa: PERF401
                    await asyncio.start_server(lambda r, w: None, host="127.0.0.1", port=first + offset)
                )
        except OSError:
            for server in held:
                server.close()
                await server.wait_closed()
            continue
        landing = held.pop()
        landing.close()
        await landing.wait_closed()
        return first, held
    raise AssertionError(f"no run of {count + 1} consecutive free loopback ports")


async def close_listener(server: asyncio.Server) -> None:
    """Close a server something may still be connecting to, and every connection it holds.

    The same order as :meth:`host.server.HostServer.stop`, for the reasons stated there.
    """
    await asyncio.sleep(0)
    server.close()
    server.close_clients()
    await server.wait_closed()


async def close_all(servers: list[asyncio.Server]) -> None:
    """Close every server and wait for it, so the next test finds the ports free."""
    for server in servers:
        server.close()
        await server.wait_closed()


class RunningHost:
    """A started server plus the pieces a test needs to make assertions about it."""

    def __init__(self, server: HostServer, plugin: FakePlugin, events: EventSink, static_root: str) -> None:
        self.server = server
        self.plugin = plugin
        self.events = events
        self.static_root = static_root

    @property
    def port(self) -> int:
        return self.server.port

    @property
    def token(self) -> str:
        return self.server.token


@pytest.fixture
async def running_host(tmp_path) -> AsyncIterator[RunningHost]:
    """Start a host on a free port serving ``tmp_path/dist``; stop it after the test."""
    logger = logging.getLogger("test_host")
    static_root = tmp_path / "dist"
    static_root.mkdir()
    (static_root / "index.js").write_text("export const panel = 1;\n", encoding="utf-8")

    plugin = FakePlugin()
    events = EventSink(logger)
    server = HostServer(
        dispatcher=CallDispatcher(plugin, logger),
        events=events,
        static_root=str(static_root),
        logger=logger,
        server_identity=SERVER_IDENTITY,
        preferred_port=free_port(),
    )
    await server.start()
    try:
        yield RunningHost(server, plugin, events, str(static_root))
    finally:
        await server.stop()
