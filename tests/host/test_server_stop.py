"""Stopping the server, with a connection that was accepted and never spoke."""

from __future__ import annotations

import asyncio
import logging
import socket

from host.dispatch import CallDispatcher
from host.events import EventSink
from host.server import HostServer
from tests.host.conftest import SERVER_IDENTITY, FakeEndpoints, free_port, nothing_left_for_the_collector

LOGGER = logging.getLogger("test_server_stop")

# Far above what a stop takes, far below what a stuck one would cost the suite.
STOP_BOUND_SECONDS = 2.0


def _server(static_root: str) -> HostServer:
    return HostServer(
        dispatcher=CallDispatcher(FakeEndpoints(), LOGGER),
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
            # Many loop turns, where the accept takes two: the connection is
            # attached and its handler is waiting for a request head.
            await asyncio.sleep(0.1)
            await asyncio.wait_for(server.stop(), STOP_BOUND_SECONDS)
        finally:
            client.close()

    async def test_a_connection_accepted_in_the_tick_before_the_stop_is_closed_rather_than_abandoned(self, tmp_path):
        """Rests on the scheduling order :func:`host.server.stop_accepting` states, which ``stop()`` calls.

        Two yields after the connect land the accept in the turn before the stop.
        """
        server = _server(str(tmp_path))
        await server.start()
        async with nothing_left_for_the_collector():
            client = socket.create_connection(("127.0.0.1", server.port))
            try:
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                await asyncio.wait_for(server.stop(), STOP_BOUND_SECONDS)
            finally:
                client.close()

    async def test_a_connection_accepted_as_the_stop_closes_leaves_nothing_for_the_collector(self, tmp_path):
        """Rests on the same scheduling order, with one yield before the stop rather than two.

        That has the listener's accept queued in the turn ``stop()`` is entered in.
        """
        server = _server(str(tmp_path))
        await server.start()
        async with nothing_left_for_the_collector():
            client = socket.create_connection(("127.0.0.1", server.port))
            try:
                await asyncio.sleep(0)
                await asyncio.wait_for(server.stop(), STOP_BOUND_SECONDS)
            finally:
                client.close()


class TestClosingByHand:
    """The close a stop makes where ``asyncio.Server`` has no ``close_clients()``, run on every version."""

    async def test_a_connection_being_served_is_closed(self, tmp_path):
        server = _server(str(tmp_path))
        await server.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        try:
            # The handler is waiting for a request head that never comes.
            await asyncio.sleep(0.1)
            server._close_clients_by_hand()
            assert await asyncio.wait_for(reader.read(), STOP_BOUND_SECONDS) == b""
        finally:
            writer.close()
            await server.stop()

    async def test_a_handler_that_starts_after_it_closes_its_own_socket(self, tmp_path):
        server = _server(str(tmp_path))
        await server.start()
        server._close_clients_by_hand()
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        try:
            assert await asyncio.wait_for(reader.read(), STOP_BOUND_SECONDS) == b""
        finally:
            writer.close()
            await server.stop()
