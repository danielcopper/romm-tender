"""The protocol, over a real socket: handshake, calls, events, caps, loss."""

from __future__ import annotations

import asyncio
import json

import pytest

from host.connection import MAX_FRAME_BYTES
from host.dispatch import DEFAULT_PAYLOAD_LIMIT
from host.protocol import (
    REASON_BACKEND_EXCEPTION,
    REASON_METHOD_UNKNOWN,
    REASON_PAYLOAD_TOO_LARGE,
    TYPE_ERROR,
    TYPE_EVENT,
    TYPE_REPLY,
)
from lib.websocket_frames import (
    CLOSE_MESSAGE_TOO_BIG,
    CLOSE_NORMAL,
    OPCODE_BINARY,
    OPCODE_CLOSE,
    OPCODE_CONTINUATION,
    OPCODE_PING,
    OPCODE_PONG,
    OPCODE_TEXT,
    accept_key,
    build_frame,
    text_frame,
)
from tests.host.ws_client import WsTestClient, http_get


def _close_payload(code: int) -> bytes:
    """A close frame's body: the two-byte code, and nothing after it."""
    return code.to_bytes(2, "big")


def _oversized_header(length: int, *, fin: bool, opcode: int = OPCODE_TEXT) -> bytes:
    """A masked frame header announcing *length* bytes — and no payload at all.

    Built by hand rather than through :func:`build_frame`, because the point is
    to put a header on the wire that no payload follows: the server must refuse
    it on the announcement, which is the only moment at which refusing saves the
    memory.
    """
    return (
        ((0x80 if fin else 0x00) | opcode).to_bytes(1, "big")
        + (0x80 | 127).to_bytes(1, "big")
        + length.to_bytes(8, "big")
        + b"\x01\x02\x03\x04"
    )


class TestHandshake:
    async def test_it_upgrades_and_answers_the_key(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            assert client.handshake["upgrade"] == "websocket"
            assert client.handshake["connection"].lower() == "upgrade"
            assert client.handshake["sec-websocket-accept"]
        finally:
            await client.close()

    def test_the_answer_is_the_digest_of_the_offered_key(self):
        """Pinned against RFC 6455's own example, so the handshake proves comprehension."""
        assert accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="

    async def test_a_plain_get_on_the_socket_path_is_refused(self, running_host):
        status, _, _ = await http_get(
            running_host.port,
            "/ws",
            token=running_host.token,
        )

        assert status == 426

    async def test_a_protocol_version_other_than_13_is_refused(self, running_host):
        with pytest.raises(AssertionError, match="refused with 426"):
            await WsTestClient.connect(running_host.port, running_host.token, version="8")


class TestCallAndReply:
    async def test_a_call_comes_back_as_a_reply_with_its_own_id(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            answer = await client.call(7, "echo", ["hello"])
        finally:
            await client.close()

        assert answer == {"type": TYPE_REPLY, "id": 7, "result": {"echo": "hello"}}

    async def test_arguments_arrive_in_order(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            await client.call("a", "echo", [{"nested": [1, 2]}])
        finally:
            await client.close()

        assert running_host.plugin.calls == [("echo", ({"nested": [1, 2]},))]

    async def test_a_call_with_no_arguments_is_answered(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            answer = await client.call(1, "no_arguments", [])
        finally:
            await client.close()

        assert answer["result"] == "answered"

    async def test_two_calls_are_both_answered(self, running_host):
        """Calls are independent: neither waits on the other's method."""
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            await client.send_json({"type": "call", "id": 1, "method": "echo", "args": ["first"]})
            await client.send_json({"type": "call", "id": 2, "method": "echo", "args": ["second"]})
            answers = {frame["id"]: frame["result"]["echo"] for frame in [await client.recv_json() for _ in range(2)]}
        finally:
            await client.close()

        assert answers == {1: "first", 2: "second"}


class TestTransportErrors:
    async def test_an_unknown_method_is_a_transport_error(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            answer = await client.call(3, "no_such_method")
        finally:
            await client.close()

        assert answer["type"] == TYPE_ERROR
        assert answer["reason"] == REASON_METHOD_UNKNOWN
        assert answer["id"] == 3
        assert "traceback" not in answer

    @pytest.mark.parametrize("method", ["_private", "synchronous"])
    async def test_an_unreachable_method_is_not_reachable_over_the_wire(self, running_host, method):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            answer = await client.call(4, method)
        finally:
            await client.close()

        assert answer["reason"] == REASON_METHOD_UNKNOWN

    async def test_a_raising_method_answers_with_a_traceback(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            answer = await client.call(5, "boom")
        finally:
            await client.close()

        assert answer["reason"] == REASON_BACKEND_EXCEPTION
        assert "the backend broke" in answer["message"]
        assert "ValueError" in answer["traceback"]

    async def test_an_unencodable_answer_is_a_transport_error(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            answer = await client.call(6, "unserialisable")
        finally:
            await client.close()

        assert answer["reason"] == REASON_BACKEND_EXCEPTION

    async def test_a_transport_error_keeps_the_connection(self, running_host):
        """One bad call must not cost every other call on the socket."""
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            await client.call(1, "boom")
            answer = await client.call(2, "echo", ["still here"])
        finally:
            await client.close()

        assert answer["result"] == {"echo": "still here"}


class TestTheTwoCaps:
    async def test_an_oversized_answer_is_refused_for_that_call_alone(self, running_host):
        """The whole reason the answer cap is not the frame cap."""
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            answer = await client.call(1, "blob", [DEFAULT_PAYLOAD_LIMIT + 1])
            after = await client.call(2, "echo", ["connection lives"])
        finally:
            await client.close()

        assert answer["reason"] == REASON_PAYLOAD_TOO_LARGE
        assert after["result"] == {"echo": "connection lives"}

    async def test_an_answer_under_the_cap_is_sent_whole(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            answer = await client.call(1, "blob", [8 * 1024 * 1024])
        finally:
            await client.close()

        assert len(answer["result"]["blob"]) == 8 * 1024 * 1024

    async def test_an_oversized_frame_is_refused_before_its_payload_is_read(self, running_host):
        """The cap is judged on the announced length: no payload is ever sent."""
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            # A header announcing more than the cap, and not one byte of body.
            await client.send_raw(_oversized_header(MAX_FRAME_BYTES + 1, fin=True))
            opcode, payload = await client.recv_frame()
        finally:
            await client.close()

        assert opcode == OPCODE_CLOSE
        assert int.from_bytes(payload[:2], "big") == CLOSE_MESSAGE_TOO_BIG

    async def test_fragments_are_capped_on_their_running_total(self, running_host):
        """Each fragment fits; together they do not. A cap after assembly would pass."""
        half = MAX_FRAME_BYTES // 2 + 1
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            await client.send_raw(build_frame(OPCODE_TEXT, b"x" * half, fin=False, mask=b"\x01\x02\x03\x04"))
            await client.send_raw(_oversized_header(half, fin=True, opcode=OPCODE_CONTINUATION))
            opcode, payload = await client.recv_frame()
        finally:
            await client.close()

        assert opcode == OPCODE_CLOSE
        assert int.from_bytes(payload[:2], "big") == CLOSE_MESSAGE_TOO_BIG


class TestFragmentation:
    async def test_a_message_split_across_frames_is_reassembled(self, running_host):
        message = json.dumps({"type": "call", "id": 9, "method": "echo", "args": ["split"]}).encode("utf-8")
        cut = len(message) // 2
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            await client.send_raw(build_frame(OPCODE_TEXT, message[:cut], fin=False, mask=b"\x01\x02\x03\x04"))
            await client.send_raw(build_frame(OPCODE_CONTINUATION, message[cut:], mask=b"\x05\x06\x07\x08"))
            answer = await client.recv_json()
        finally:
            await client.close()

        assert answer["result"] == {"echo": "split"}


class TestUnknownMessages:
    @pytest.mark.parametrize(
        "message",
        [
            {"type": "greeting", "id": 1},
            {"type": "reply", "id": 2, "result": None},
            {"id": 3, "method": "echo", "args": []},
        ],
        ids=["unknown type", "a type the backend never receives", "no type at all"],
    )
    async def test_it_is_dropped_and_the_connection_is_kept(self, running_host, message):
        """Closing would reject every other call in flight — the wrong punishment."""
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            await client.send_json(message)
            answer = await client.call(99, "echo", ["alive"])
        finally:
            await client.close()

        assert answer["result"] == {"echo": "alive"}

    @pytest.mark.parametrize(
        "message",
        [
            {"type": "call", "method": "echo", "args": []},
            {"type": "call", "id": 1, "args": []},
            {"type": "call", "id": 1, "method": "echo", "args": "not a list"},
        ],
        ids=["no id", "no method", "args are not a list"],
    )
    async def test_a_malformed_call_is_dropped_too(self, running_host, message):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            await client.send_json(message)
            answer = await client.call(98, "echo", ["alive"])
        finally:
            await client.close()

        assert answer["result"] == {"echo": "alive"}

    async def test_a_frame_that_is_not_json_is_dropped(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            await client.send_text("{not json")
            answer = await client.call(97, "echo", ["alive"])
        finally:
            await client.close()

        assert answer["result"] == {"echo": "alive"}

    async def test_every_dropped_message_is_counted(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            await client.send_json({"type": "greeting"})
            await client.send_text("{not json")
            await client.call(1, "echo", ["settle"])
        finally:
            await client.close()

        assert running_host.server.dropped_messages == 2

    async def test_the_count_survives_the_connection_that_earned_it(self, running_host):
        """A reconnect is exactly when this counter has something to say.

        The development loop swaps the frontend without restarting the backend,
        which is a new connection — so a per-connection count would reset at the
        moment a panel and backend first disagreed about the wire.
        """
        first = await WsTestClient.connect(running_host.port, running_host.token, session="panel-1")
        await first.send_json({"type": "greeting"})
        await first.call(1, "echo", ["settle"])
        await first.close()
        await asyncio.sleep(0.05)

        second = await WsTestClient.connect(running_host.port, running_host.token, session="panel-2")
        try:
            await second.send_json({"type": "greeting"})
            await second.call(2, "echo", ["settle"])
        finally:
            await second.close()

        assert running_host.server.dropped_messages == 2

    async def test_a_binary_frame_closes_the_connection(self, running_host):
        """Every message here is text; a binary frame is a protocol disagreement."""
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            await client.send_raw(build_frame(OPCODE_BINARY, b"\x00\x01", mask=b"\x01\x02\x03\x04"))
            opcode, _ = await client.recv_frame()
        finally:
            await client.close()

        assert opcode == OPCODE_CLOSE

    async def test_an_unmasked_client_frame_closes_the_connection(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            await client.send_raw(text_frame('{"type":"call","id":1,"method":"echo","args":[]}'))
            opcode, _ = await client.recv_frame()
        finally:
            await client.close()

        assert opcode == OPCODE_CLOSE


class TestControlFrames:
    async def test_a_ping_is_answered_with_a_pong_carrying_the_payload(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            await client.send_raw(build_frame(OPCODE_PING, b"beat", mask=b"\x01\x02\x03\x04"))
            opcode, payload = await client.recv_frame()
        finally:
            await client.close()

        assert (opcode, payload) == (OPCODE_PONG, b"beat")

    async def test_a_close_frame_is_answered_and_ends_the_connection(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            await client.send_raw(build_frame(OPCODE_CLOSE, _close_payload(CLOSE_NORMAL), mask=b"\x01\x02\x03\x04"))
            opcode, payload = await client.recv_frame()
        finally:
            await client.close()

        assert opcode == OPCODE_CLOSE
        assert int.from_bytes(payload[:2], "big") == CLOSE_NORMAL
        await asyncio.sleep(0.05)
        assert not running_host.server.connected


class TestEvents:
    async def test_an_event_reaches_the_connected_panel(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            await client.call(1, "echo", ["settle"])
            delivered = await running_host.events.emit("sync_complete", {"total_games": 3})
            frame = await client.recv_json()
        finally:
            await client.close()

        assert delivered is True
        assert frame == {"type": TYPE_EVENT, "name": "sync_complete", "payload": {"total_games": 3}}

    async def test_an_event_with_no_panel_is_dropped_and_says_so(self, running_host):
        delivered = await running_host.events.emit("sync_complete", {"total_games": 3})

        assert delivered is False
        assert running_host.events.dropped == 1

    async def test_nothing_is_held_for_the_next_connection(self, running_host):
        """No reply store, and no event store either."""
        await running_host.events.emit("sync_complete", {"total_games": 3})

        client = await WsTestClient.connect(running_host.port, running_host.token)
        try:
            answer = await client.call(1, "echo", ["first thing on this socket"])
        finally:
            await client.close()

        assert answer["type"] == TYPE_REPLY


class TestNewestConnectionWins:
    async def test_a_second_connection_displaces_the_first(self, running_host):
        first = await WsTestClient.connect(running_host.port, running_host.token, session="panel-1")
        await first.call(1, "echo", ["first"])
        second = await WsTestClient.connect(running_host.port, running_host.token, session="panel-2")
        try:
            answer = await second.call(2, "echo", ["second"])
            opcode, _ = await first.recv_frame()
        finally:
            await first.close()
            await second.close()

        assert answer["result"] == {"echo": "second"}
        assert opcode == OPCODE_CLOSE

    async def test_events_follow_the_newest_connection(self, running_host):
        first = await WsTestClient.connect(running_host.port, running_host.token, session="panel-1")
        await first.call(1, "echo", ["settle"])
        second = await WsTestClient.connect(running_host.port, running_host.token, session="panel-2")
        try:
            await second.call(2, "echo", ["settle"])
            delivered = await running_host.events.emit("sync_complete", {"total_games": 1})
            frame = await second.recv_json()
        finally:
            await first.close()
            await second.close()

        assert delivered is True
        assert frame["name"] == "sync_complete"

    async def test_the_displaced_connections_teardown_does_not_silence_events(self, running_host):
        """The older socket tears down after its replacement has attached."""
        first = await WsTestClient.connect(running_host.port, running_host.token, session="panel-1")
        await first.call(1, "echo", ["settle"])
        second = await WsTestClient.connect(running_host.port, running_host.token, session="panel-2")
        try:
            await second.call(2, "echo", ["settle"])
            await first.close()
            await asyncio.sleep(0.05)
            delivered = await running_host.events.emit("sync_complete", {"total_games": 1})
        finally:
            await second.close()

        assert delivered is True


class TestConnectionLoss:
    async def test_a_call_in_flight_is_cancelled_when_the_socket_goes(self, running_host):
        """Its answer has nowhere to go, so it is not left running into a dead write."""
        client = await WsTestClient.connect(running_host.port, running_host.token)
        await client.send_json({"type": "call", "id": 1, "method": "never_returns", "args": []})
        await asyncio.wait_for(running_host.plugin.entered.wait(), 5)

        await client.close()

        await asyncio.wait_for(running_host.plugin.cancelled.wait(), 5)
        assert running_host.plugin.cancelled.is_set()

    async def test_the_host_reports_no_panel_once_the_socket_is_gone(self, running_host):
        client = await WsTestClient.connect(running_host.port, running_host.token)
        await client.call(1, "echo", ["settle"])

        await client.close()
        await asyncio.sleep(0.05)

        assert not running_host.server.connected
        assert await running_host.events.emit("sync_complete", {}) is False
