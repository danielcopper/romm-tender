"""Stopping the server, with a connection that was accepted and never spoke."""

from __future__ import annotations

import asyncio
import gc
import logging
import socket
import sys

from host.dispatch import CallDispatcher
from host.events import EventSink
from host.server import HostServer
from tests.host.conftest import SERVER_IDENTITY, FakePlugin, free_port

LOGGER = logging.getLogger("test_server_stop")

# Far above what a stop takes, far below what a stuck one would cost the suite.
STOP_BOUND_SECONDS = 2.0


def _server(static_root: str) -> HostServer:
    return HostServer(
        dispatcher=CallDispatcher(FakePlugin(), LOGGER),
        events=EventSink(LOGGER),
        static_root=static_root,
        logger=LOGGER,
        server_identity=SERVER_IDENTITY,
        preferred_port=free_port(),
    )


class TestStop:
    async def test_an_accepted_connection_that_never_speaks_does_not_hold_the_stop(self, tmp_path):
        server = _server(str(tmp_path))
        await server.start()
        client = socket.create_connection(("127.0.0.1", server.port))
        try:
            # Many loop iterations, where the accept takes two: the connection is
            # attached and its handler is waiting for a request head.
            await asyncio.sleep(0.1)
            await asyncio.wait_for(server.stop(), STOP_BOUND_SECONDS)
        finally:
            client.close()

    async def test_a_connection_accepted_in_the_tick_before_the_stop_is_closed_rather_than_abandoned(
        self, tmp_path, monkeypatch
    ):
        """Rests on the scheduling order ``HostServer.stop()`` states at its yield.

        Two yields after the connect land the accept in the tick before the stop,
        and one after it lets an abandoned accept task let go of its transport, so
        the collector reaches it here and the recorder reports it under this
        test's name rather than at the end of the session.
        """
        unraisable: list[sys.UnraisableHookArgs] = []
        monkeypatch.setattr(sys, "unraisablehook", unraisable.append)
        server = _server(str(tmp_path))
        await server.start()
        client = socket.create_connection(("127.0.0.1", server.port))
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await asyncio.wait_for(server.stop(), STOP_BOUND_SECONDS)
        finally:
            client.close()
        await asyncio.sleep(0)
        gc.collect()

        assert [repr(entry.exc_value) for entry in unraisable] == []
