"""Closing a listener something is still connecting to, the way the host tests do."""

from __future__ import annotations

import asyncio
import gc
import socket
import sys
import warnings

from tests.host.conftest import close_at_once, close_listener


class TestCloseListener:
    async def test_a_connection_accepted_as_it_closes_leaves_nothing_for_the_collector(self, monkeypatch):
        """Rests on the scheduling order ``HostServer.stop()`` states at its yield, which ``close_listener`` shares.

        One yield after the connect, and no other count, queues the accept for the
        turn the close begins in; one after the close lets an abandoned accept task
        let go of its transport, so the collector reaches it here. Python 3.13 and
        3.14 report such a transport as an unraisable ``TypeError``, and every
        version warns about it as unclosed.
        """
        unraisable: list[sys.UnraisableHookArgs] = []
        monkeypatch.setattr(sys, "unraisablehook", unraisable.append)
        server = await asyncio.start_server(close_at_once, host="127.0.0.1", port=0)
        port = server.sockets[0].getsockname()[1]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client = socket.create_connection(("127.0.0.1", port))
            try:
                await asyncio.sleep(0)
                await close_listener(server)
            finally:
                client.close()
            await asyncio.sleep(0)
            gc.collect()

        assert [repr(entry.exc_value) for entry in unraisable] == []
        assert [f"{entry.category.__name__}: {entry.message}" for entry in caught] == []
