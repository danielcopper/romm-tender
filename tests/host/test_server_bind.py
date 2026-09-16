"""Binding the port, and falling back past a port somebody else holds."""

from __future__ import annotations

import asyncio
import logging

import pytest

from host.dispatch import CallDispatcher
from host.events import EventSink
from host.server import DEFAULT_PORT, HostServer
from tests.host.conftest import SERVER_IDENTITY, FakePlugin, close_all, free_port, squat_run
from tests.host.ws_client import http_get

LOGGER = logging.getLogger("test_server_bind")


def build_server(static_root, port: int, attempts: int = 32) -> HostServer:
    return HostServer(
        dispatcher=CallDispatcher(FakePlugin(), LOGGER),
        events=EventSink(LOGGER),
        static_root=str(static_root),
        logger=LOGGER,
        server_identity=SERVER_IDENTITY,
        preferred_port=port,
        port_attempts=attempts,
    )


@pytest.fixture
def static_root(tmp_path):
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.js").write_text("export const panel = 1;\n", encoding="utf-8")
    return root


class TestTheBind:
    def test_the_default_port_is_the_one_the_design_names(self):
        assert DEFAULT_PORT == 27737

    async def test_it_takes_the_port_it_asked_for(self, static_root):
        wanted = free_port()
        server = build_server(static_root, wanted)
        try:
            assert await server.start() == wanted
            assert server.port == wanted
        finally:
            await server.stop()

    async def test_the_port_is_zero_before_it_starts(self, static_root):
        assert build_server(static_root, free_port()).port == 0

    async def test_it_falls_back_past_a_port_another_program_holds(self, static_root):
        """The fallback is for a FOREIGN program — a second backend never reaches it."""
        taken, squatters = await squat_run(1)
        server = build_server(static_root, taken)
        try:
            assert await server.start() == taken + 1
        finally:
            await server.stop()
            await close_all(squatters)

    async def test_it_keeps_stepping_past_a_run_of_taken_ports(self, static_root):
        first, squatters = await squat_run(3)
        server = build_server(static_root, first)
        try:
            assert await server.start() == first + 3
        finally:
            await server.stop()
            await close_all(squatters)

    async def test_it_raises_when_no_port_in_the_range_is_free(self, static_root):
        taken = free_port()
        squatter = await asyncio.start_server(lambda r, w: None, host="127.0.0.1", port=taken)
        server = build_server(static_root, taken, attempts=1)
        try:
            with pytest.raises(OSError, match="no free port"):
                await server.start()
        finally:
            squatter.close()
            await squatter.wait_closed()

    async def test_the_checks_are_built_from_the_port_actually_bound(self, static_root):
        """A policy built from the port we wanted would refuse everything after a fallback."""
        taken, squatters = await squat_run(1)
        server = build_server(static_root, taken)
        try:
            bound = await server.start()
            status, _, _ = await http_get(bound, "/index.js", token=server.token, host=f"127.0.0.1:{bound}")
            refused, _, _ = await http_get(bound, "/index.js", token=server.token, host=f"127.0.0.1:{taken}")
        finally:
            await server.stop()
            await close_all(squatters)

        assert status == 200
        assert refused == 421

    async def test_it_binds_loopback_only(self, static_root):
        """Nothing off this machine can reach the backend, whatever the checks say."""
        server = build_server(static_root, free_port())
        try:
            await server.start()
            assert set(server.bound_addresses) == {"127.0.0.1"}
        finally:
            await server.stop()

    async def test_it_is_listening_nowhere_before_it_starts(self, static_root):
        assert build_server(static_root, free_port()).bound_addresses == ()


class TestTheHandshakeIsRefusedRatherThanRaising:
    async def test_a_non_ascii_key_is_refused(self, static_root):
        """Latin-1 decodes every byte, so such a key reaches the handshake intact.

        Answering it means digesting its ASCII bytes, which raises — and out of
        a connection callback that is a bare traceback in asyncio's log with the
        socket left open, rather than an answer.
        """
        server = build_server(static_root, free_port())
        try:
            port = await server.start()
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            request = (
                f"GET /ws?token={server.token} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Key: schlüssel-mit-umlaut\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode("latin-1")
            writer.write(request)
            await writer.drain()
            status_line = await asyncio.wait_for(reader.readline(), 5)
            writer.close()
        finally:
            await server.stop()

        assert b"426" in status_line


class TestTheLoadAddress:
    async def test_it_names_the_bound_port_and_carries_the_token(self, static_root):
        server = build_server(static_root, free_port())
        try:
            port = await server.start()
            url = server.bundle_url()
        finally:
            await server.stop()

        assert url == f"http://127.0.0.1:{port}/index.js?token={server.token}"

    async def test_the_address_it_prints_actually_serves_the_bundle(self, static_root):
        """The one line the development loop is driven by, checked end to end."""
        server = build_server(static_root, free_port())
        try:
            await server.start()
            url = server.bundle_url()
            path = url.split(f"{server.port}", 1)[1].split("?")[0]
            status, _, body = await http_get(server.port, path, token=server.token)
        finally:
            await server.stop()

        assert (status, body) == (200, b"export const panel = 1;\n")


class TestStopping:
    async def test_a_stopped_server_no_longer_answers(self, static_root):
        server = build_server(static_root, free_port())
        port = await server.start()
        await server.stop()

        with pytest.raises(OSError):
            await http_get(port, "/index.js", token=server.token)

    async def test_stopping_twice_is_harmless(self, static_root):
        server = build_server(static_root, free_port())
        await server.start()
        await server.stop()

        await server.stop()

    async def test_stopping_one_that_never_started_is_harmless(self, static_root):
        await build_server(static_root, free_port()).stop()
