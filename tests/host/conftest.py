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
