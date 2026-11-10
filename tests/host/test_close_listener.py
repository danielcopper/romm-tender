"""Closing a listener something is still connecting to, the way the host tests do."""

from __future__ import annotations

import asyncio
import socket

from tests.host.conftest import close_at_once, close_listener, nothing_left_for_the_collector


class TestCloseListener:
    async def test_a_connection_accepted_as_it_closes_leaves_nothing_for_the_collector(self):
        """Rests on the scheduling order :func:`host.server.close_without_abandoning` states.

        One yield after the connect, and no other count, has the listener's accept
        queued in the turn ``close_listener`` is entered in. With only a yield in
        place of ``close_without_abandoning``'s removal of the reader, that accept
        runs during the yield, and ``close()`` follows a turn later, ahead of the
        task building the accepted connection's transport.
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
