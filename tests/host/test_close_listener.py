"""Closing a listener something is still connecting to, the way the host tests do."""

from __future__ import annotations

import asyncio
import socket

from tests.host.conftest import close_at_once, close_listener, nothing_left_for_the_collector


class TestCloseListener:
    async def test_a_connection_accepted_as_it_closes_leaves_nothing_for_the_collector(self):
        """Rests on the scheduling order :func:`host.server.stop_accepting` states, which ``close_listener`` calls.

        One yield after the connect, and no other count, queues the accept for the
        turn the close begins in.
        """
        server = await asyncio.start_server(close_at_once, host="127.0.0.1", port=0)
        port = server.sockets[0].getsockname()[1]
        async with nothing_left_for_the_collector():
            client = socket.create_connection(("127.0.0.1", port))
            try:
                await asyncio.sleep(0)
                await close_listener(server)
            finally:
                client.close()
