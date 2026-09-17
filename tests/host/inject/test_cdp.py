"""The debugger client: what it finds, what it carries, and what a loss costs."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from host.inject.cdp import (
    CdpConnection,
    CdpConnectionLost,
    CdpUnavailableError,
    Target,
    find_shared_context,
    list_targets,
    pages_besides,
    parse_targets,
    split_ws_url,
    unnamed_targets,
)
from tests.host.conftest import free_port
from tests.host.inject.fake_debugger import FakeDebugger, FakeTarget, never_answer, refuse

LOGGER = logging.getLogger("test_cdp")


@pytest.fixture
async def debugger():
    server = FakeDebugger()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


async def connect_to(server: FakeDebugger, target_id: str = "renderer") -> CdpConnection:
    return await CdpConnection.connect(f"ws://127.0.0.1:{server.port}/devtools/page/{target_id}", logger=LOGGER)


async def ask(connection: CdpConnection, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send one command with a bound on the answer.

    ``CdpConnection.call`` deliberately has no timeout of its own — the caller
    owns that wait — and nothing in this repo's pytest run imposes one either.
    Called bare, a client that stopped answering would HANG the suite instead of
    failing it, which is what happened when the frame reassembly was mutated.
    """
    async with asyncio.timeout(5.0):
        return await connection.call(method, params)


class TestTheDiscoveryRule:
    def test_a_target_with_no_title_yet_is_not_the_renderer(self):
        targets = (Target(id="a", title="", type="page", websocket_url="ws://x/a"),)
        assert find_shared_context(targets) is None

    def test_the_same_target_is_the_renderer_once_steam_names_it(self):
        before = (Target(id="a", title="", type="page", websocket_url="ws://x/a"),)
        after = (Target(id="a", title="SharedJSContext", type="page", websocket_url="ws://x/a"),)
        assert find_shared_context(before) is None
        found = find_shared_context(after)
        assert found is not None
        assert found.id == "a"

    def test_an_unnamed_target_is_reported_apart_from_an_empty_list(self):
        targets = (Target(id="a", title="", type="page", websocket_url="ws://x/a"),)
        assert len(unnamed_targets(targets)) == 1
        assert unnamed_targets(()) == ()

    def test_rows_that_cannot_be_connected_to_are_dropped(self):
        parsed = parse_targets(
            [
                {"id": "a", "title": "SharedJSContext", "type": "page", "webSocketDebuggerUrl": "ws://x/a"},
                {"id": "b", "title": "no socket", "type": "page"},
                {"title": "no id", "webSocketDebuggerUrl": "ws://x/c"},
                "not a row",
            ]
        )
        assert [target.id for target in parsed] == ["a"]

    def test_a_row_with_no_title_keeps_the_empty_string(self):
        parsed = parse_targets([{"id": "a", "webSocketDebuggerUrl": "ws://x/a"}])
        assert parsed[0].title == ""
        assert parsed[0].type == ""

    def test_an_answer_that_is_not_a_list_is_refused(self):
        with pytest.raises(CdpUnavailableError):
            parse_targets({"targets": []})

    def test_the_alive_count_discounts_the_renderer_by_id_not_by_title(self):
        targets = (
            Target(id="renderer", title="SharedJSContext", type="page", websocket_url="ws://x/a"),
            Target(id="bpm", title="", type="page", websocket_url="ws://x/b"),
            Target(id="worker", title="a worker", type="other", websocket_url="ws://x/c"),
        )
        assert [target.id for target in pages_besides(targets, "renderer")] == ["bpm"]

    def test_a_websocket_address_splits_into_host_port_and_path(self):
        assert split_ws_url("ws://127.0.0.1:8080/devtools/page/AB") == ("127.0.0.1", 8080, "/devtools/page/AB")
        assert split_ws_url("ws://localhost/devtools/page/AB") == ("localhost", 80, "/devtools/page/AB")


class TestListingTargets:
    async def test_it_reads_what_the_debugger_has_open(self, debugger):
        debugger.targets = [FakeTarget(id="renderer", title="SharedJSContext")]
        targets = await list_targets(debugger.port)
        assert [target.title for target in targets] == ["SharedJSContext"]
        assert targets[0].websocket_url.endswith("/devtools/page/renderer")

    async def test_nothing_listening_is_reported_as_unavailable(self):
        port = free_port()
        with pytest.raises(CdpUnavailableError):
            await list_targets(port)

    async def test_an_answer_that_is_not_json_is_reported_as_unavailable(self, debugger):
        debugger.answer_json = "<html>not the debugger</html>"
        with pytest.raises(CdpUnavailableError):
            await list_targets(debugger.port)


class TestTheConnection:
    async def test_a_command_is_answered_by_its_own_id(self, debugger):
        debugger.handlers["Page.enable"] = lambda _params: {"enabled": True}
        connection = await connect_to(debugger)
        try:
            assert await ask(connection, "Page.enable") == {"enabled": True}
        finally:
            await connection.close()

    async def test_a_refused_command_raises_rather_than_answering_empty(self, debugger):
        debugger.handlers["Page.enable"] = lambda _params: refuse("no such domain")
        connection = await connect_to(debugger)
        try:
            with pytest.raises(CdpUnavailableError, match="no such domain"):
                await ask(connection, "Page.enable")
        finally:
            await connection.close()

    async def test_an_event_arriving_before_anyone_waits_is_still_delivered(self, debugger):
        connection = await connect_to(debugger)
        try:
            queue = connection.subscribe("Page.domContentEventFired")
            await debugger.emit("Page.domContentEventFired", {"timestamp": 1})
            event = await asyncio.wait_for(queue.get(), 5)
            assert event is not None
            assert event["method"] == "Page.domContentEventFired"
        finally:
            await connection.close()

    async def test_an_unsubscribed_event_is_dropped_rather_than_queued(self, debugger):
        connection = await connect_to(debugger)
        try:
            queue = connection.subscribe("Page.domContentEventFired")
            await debugger.emit("Page.loadEventFired", {})
            debugger.handlers["Page.enable"] = lambda _params: {}
            await ask(connection, "Page.enable")
            assert queue.empty()
        finally:
            await connection.close()

    async def test_a_message_split_across_frames_is_reassembled(self, debugger):
        """The reply arrives as a text frame with FIN clear plus a continuation.

        A reply this size never fragments by itself, so the fake is told to
        split this one — without that the reassembly is dead code under a test
        name that says otherwise.
        """
        payload = {"x": "y" * 200}
        debugger.handlers["Page.enable"] = lambda _params: payload
        debugger.fragment_method = "Page.enable"
        connection = await connect_to(debugger)
        try:
            assert await ask(connection, "Page.enable") == payload
        finally:
            await connection.close()

    async def test_a_fragmented_message_is_two_frames_on_the_wire(self, debugger):
        """The fake really splits it — otherwise the test above proves nothing."""
        frames = 0
        original = debugger._serve_frames

        async def counting(reader, writer):
            class Counting:
                def __getattr__(self, name):
                    return getattr(writer, name)

                def write(self, data):
                    nonlocal frames
                    frames += 1
                    writer.write(data)

            await original(reader, Counting())

        debugger._serve_frames = counting
        debugger.handlers["Page.enable"] = lambda _params: {"x": "y" * 200}
        debugger.fragment_method = "Page.enable"
        connection = await connect_to(debugger)
        try:
            await ask(connection, "Page.enable")
            assert frames == 2
        finally:
            await connection.close()

    async def test_giving_up_on_one_answer_leaves_the_next_command_working(self, debugger):
        """The spike's defect: a short wait must not poison the connection.

        ``spike/inject.py`` set the socket's own timeout to listen, never put it
        back, and left a half-read frame behind when the listen expired. Here the
        wait is the caller's, so a command that is abandoned costs the frame
        stream nothing.
        """
        debugger.handlers["Runtime.evaluate"] = never_answer
        connection = await connect_to(debugger)
        try:
            abandoned = connection.call("Runtime.evaluate", {"expression": "1"})
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(abandoned, 0.2)

            debugger.handlers["Page.enable"] = lambda _params: {"still": "here"}
            assert await ask(connection, "Page.enable") == {"still": "here"}
        finally:
            await connection.close()

    async def test_an_expired_event_wait_leaves_the_next_command_working(self, debugger):
        connection = await connect_to(debugger)
        try:
            queue = connection.subscribe("Page.domContentEventFired")
            expiring = queue.get()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(expiring, 0.2)

            debugger.handlers["Page.enable"] = lambda _params: {"still": "here"}
            assert await ask(connection, "Page.enable") == {"still": "here"}
        finally:
            await connection.close()

    async def test_a_lost_connection_fails_the_command_in_flight(self, debugger):
        debugger.handlers["Runtime.evaluate"] = never_answer
        connection = await connect_to(debugger)
        try:
            pending = asyncio.ensure_future(connection.call("Runtime.evaluate", {"expression": "1"}))
            await asyncio.sleep(0.05)
            await debugger.drop_connections()
            with pytest.raises(CdpConnectionLost):
                await asyncio.wait_for(pending, 5)
        finally:
            await connection.close()

    async def test_a_lost_connection_tells_every_subscriber(self, debugger):
        connection = await connect_to(debugger)
        try:
            queue = connection.subscribe("Page.domContentEventFired")
            await debugger.drop_connections()
            assert await asyncio.wait_for(queue.get(), 5) is None
            assert connection.closed
        finally:
            await connection.close()

    async def test_a_command_on_a_closed_connection_is_refused_rather_than_sent(self, debugger):
        connection = await connect_to(debugger)
        await connection.close()
        with pytest.raises(CdpConnectionLost):
            await connection.call("Page.enable")

    async def test_a_refused_handshake_is_reported_as_unavailable(self):
        async def answer_400(_reader, writer):
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(answer_400, host="127.0.0.1", port=0)
        port = server.sockets[0].getsockname()[1]
        try:
            with pytest.raises(CdpUnavailableError):
                await CdpConnection.connect(f"ws://127.0.0.1:{port}/devtools/page/x", logger=LOGGER)
        finally:
            server.close()
            await server.wait_closed()

    async def test_nothing_listening_is_reported_as_unavailable(self):
        url = f"ws://127.0.0.1:{free_port()}/devtools/page/x"
        with pytest.raises(CdpUnavailableError):
            await CdpConnection.connect(url, logger=LOGGER)

    async def test_the_client_masks_what_it_sends(self, debugger):
        """RFC 6455 §5.1 — a server refusing an unmasked client frame is the norm."""
        seen: list[bool] = []
        original = debugger._serve_frames

        async def record(reader, writer):
            reader_ = reader

            class Watching:
                def __getattr__(self, name):
                    return getattr(reader_, name)

                async def readexactly(self, count):
                    data = await reader_.readexactly(count)
                    if count == 2:
                        seen.append(bool(data[1] & 0x80))
                    return data

            await original(Watching(), writer)

        debugger._serve_frames = record
        debugger.handlers["Page.enable"] = lambda _params: {}
        connection = await connect_to(debugger)
        try:
            await ask(connection, "Page.enable")
            assert seen
            assert all(seen)
        finally:
            await connection.close()

    async def test_every_frame_it_sends_is_masked_including_the_close(self, debugger):
        """RFC 6455 §5.1 — and ``close_frame`` builds the SERVER direction."""
        debugger.handlers["Page.enable"] = lambda _params: {}
        connection = await connect_to(debugger)
        await ask(connection, "Page.enable")
        await connection.close()
        await asyncio.sleep(0.05)

        assert debugger.unmasked_client_frames == 0

    async def test_the_debugger_is_answered_when_it_pings(self, debugger):
        debugger.handlers["Page.enable"] = lambda _params: {}
        connection = await connect_to(debugger)
        try:
            for writer in debugger._writers:
                from lib.websocket_frames import OPCODE_PING, build_frame

                writer.write(build_frame(OPCODE_PING, b"hello"))
                await writer.drain()
            await asyncio.sleep(0.05)
            assert await ask(connection, "Page.enable") == {}
        finally:
            await connection.close()

    async def test_a_message_that_is_not_a_cdp_message_is_dropped_loudly(self, debugger, caplog):
        connection = await connect_to(debugger)
        try:
            from lib.websocket_frames import OPCODE_TEXT, build_frame

            with caplog.at_level(logging.WARNING, logger="test_cdp"):
                for writer in debugger._writers:
                    writer.write(build_frame(OPCODE_TEXT, b"{not json"))
                    await writer.drain()
                await asyncio.sleep(0.05)
            assert any("not a CDP message" in record.message for record in caplog.records)
            debugger.handlers["Page.enable"] = lambda _params: {}
            assert await ask(connection, "Page.enable") == {}
        finally:
            await connection.close()


class TestTheSizeCap:
    async def test_a_frame_over_the_cap_ends_the_connection(self, debugger, monkeypatch):
        import host.inject.cdp as cdp

        monkeypatch.setattr(cdp, "MAX_MESSAGE_BYTES", 256)
        debugger.handlers["Page.enable"] = lambda _params: {"blob": "x" * 4096}
        connection = await connect_to(debugger)
        try:
            with pytest.raises(CdpConnectionLost):
                await ask(connection, "Page.enable")
        finally:
            await connection.close()


class TestWhatTheFakeDebuggerAnswers:
    """The harness itself, so a green suite is not green because of it."""

    async def test_it_speaks_the_target_list_the_client_parses(self, debugger):
        debugger.targets = [FakeTarget(id="a", title="SharedJSContext"), FakeTarget(id="b", title="")]
        rows = json.loads(json.dumps(debugger._target_rows()))
        assert {row["id"] for row in rows} == {"a", "b"}
        assert all(row["webSocketDebuggerUrl"].startswith("ws://127.0.0.1:") for row in rows)
