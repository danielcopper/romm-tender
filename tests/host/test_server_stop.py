"""Stopping the server, with a connection that was accepted and never spoke."""

from __future__ import annotations

import asyncio
import logging
import socket

from host.dispatch import CallDispatcher
from host.events import EventSink
from host.server import HostServer
from tests.host.conftest import SERVER_IDENTITY, FakePlugin, free_port

LOGGER = logging.getLogger("test_server_stop")

# Far above what a stop takes, far below what a stuck one would cost the suite.
STOP_BOUND_SECONDS = 2.0


class TestStop:
    async def test_an_accepted_connection_that_never_speaks_does_not_hold_the_stop(self, tmp_path):
        server = HostServer(
            dispatcher=CallDispatcher(FakePlugin(), LOGGER),
            events=EventSink(LOGGER),
            static_root=str(tmp_path),
            logger=LOGGER,
            server_identity=SERVER_IDENTITY,
            preferred_port=free_port(),
        )
        await server.start()
        client = socket.create_connection(("127.0.0.1", server.port))
        try:
            # Many loop iterations, where the accept takes two: the connection is
            # attached and its handler is waiting for a request head.
            await asyncio.sleep(0.1)
            await asyncio.wait_for(server.stop(), STOP_BOUND_SECONDS)
        finally:
            client.close()
