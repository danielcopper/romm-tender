"""A CEF debugger that is not CEF — real sockets, real frames, a scripted page.

The injector's whole job is talking to another program over HTTP and RFC 6455,
so the tier that checks it speaks HTTP and RFC 6455. Nothing here mocks the
transport: every test opens a real loopback port, and the frames travel through
``lib.websocket_frames`` in both directions, which is also what makes the
framing assertions mean anything.

What IS faked is the page. There is no JavaScript engine here, so
``Runtime.evaluate`` is answered by :class:`FakePage`, which recognises the three
expressions the injector sends and keeps the state they would have changed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from typing import Any

from lib.http_messages import HEAD_TERMINATOR, build_response_head, parse_request_head
from lib.websocket_frames import (
    OPCODE_CONTINUATION,
    OPCODE_PING,
    OPCODE_PONG,
    OPCODE_TEXT,
    accept_key,
    apply_mask,
    build_frame,
    header_length,
    parse_frame_header,
)
from tests.host.conftest import close_listener


@dataclass
class FakeTarget:
    """One row of the debugger's list, as the fake will report it."""

    id: str
    title: str
    type: str = "page"


@dataclass
class FakePage:
    """The state the injector's three expressions read and write.

    It answers by recognising the expression it is given, because there is no
    engine here to run one. ``bootstrap`` is every other expression: the only
    other thing the injector evaluates is the bootstrap, and it is recognised by
    exclusion rather than by a substring so a change to its text does not quietly
    stop it being seen.
    """

    marker_expression: str
    ready_expression: str
    marker: bool = False
    ready: bool = True
    bootstrap_answer: dict[str, Any] = field(default_factory=lambda: {"ok": True})
    bootstrap_raises: bool = False
    evaluated: list[str] = field(default_factory=list)

    def evaluate(self, params: dict[str, Any]) -> dict[str, Any]:
        """Answer one ``Runtime.evaluate``, recording the expression."""
        expression = params.get("expression", "")
        self.evaluated.append(expression)
        if expression == self.marker_expression:
            return {"result": {"type": "boolean", "value": self.marker}}
        if expression == self.ready_expression:
            return {"result": {"type": "boolean", "value": self.ready}}
        if self.bootstrap_raises:
            return {"result": {"type": "undefined"}, "exceptionDetails": {"text": "Uncaught SyntaxError"}}
        self.marker = True
        return {"result": {"type": "object", "value": dict(self.bootstrap_answer)}}

    @property
    def bootstraps(self) -> list[str]:
        """Every bootstrap expression this page was given, in order."""
        return [
            expression
            for expression in self.evaluated
            if expression not in (self.marker_expression, self.ready_expression)
        ]


class FakeDebugger:
    """A loopback server answering ``GET /json`` and upgrading per target."""

    def __init__(self, page: FakePage | None = None) -> None:
        self.targets: list[FakeTarget] = []
        self.page = page
        self.handlers: dict[str, Any] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.answer_json: str | None = None
        # RFC 6455 §5.1: every client frame is masked. Counted rather than
        # refused, so a test can assert on the number instead of on a hang.
        self.unmasked_client_frames = 0
        # A command to hold un-answered, so a test can stop the injector at an
        # exact point in its sequence rather than by racing it.
        self.hold_method = ""
        self.held = asyncio.Event()
        # A command whose reply goes out in two frames — a text frame with FIN
        # clear and a continuation — because a reply this small never fragments
        # on its own and the client's reassembly would otherwise go unexercised.
        self.fragment_method = ""
        self._server: asyncio.Server | None = None
        self._port = 0
        self._writers: list[asyncio.StreamWriter] = []

    @property
    def port(self) -> int:
        """The port this debugger bound."""
        return self._port

    @property
    def connections(self) -> int:
        """How many debugger sockets are open right now."""
        return len(self._writers)

    async def start(self) -> int:
        """Bind an ephemeral loopback port and start answering on it."""
        self._server = await asyncio.start_server(self._handle, host="127.0.0.1", port=0)
        self._port = self._server.sockets[0].getsockname()[1]
        return self._port

    async def stop(self) -> None:
        """Close every connection and stop listening."""
        await self.drop_connections()
        if self._server is not None:
            with contextlib.suppress(Exception):
                await close_listener(self._server)
            self._server = None

    async def drop_connections(self) -> None:
        """Cut every open debugger socket without a close frame."""
        writers, self._writers = self._writers, []
        for writer in writers:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def emit(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Push one CDP event to every connected client."""
        message = json.dumps({"method": method, "params": params or {}})
        for writer in list(self._writers):
            with contextlib.suppress(Exception):
                writer.write(build_frame(OPCODE_TEXT, message.encode("utf-8")))
                await writer.drain()

    # -- serving ---------------------------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await reader.readuntil(HEAD_TERMINATOR)
        except (asyncio.IncompleteReadError, OSError):
            return
        head = parse_request_head(raw[: -len(HEAD_TERMINATOR)])

        key = head.header("sec-websocket-key")
        if key:
            await self._upgrade(key, reader, writer)
            return

        body = (self.answer_json if self.answer_json is not None else json.dumps(self._target_rows())).encode("utf-8")
        writer.write(
            build_response_head(
                200,
                [
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(body))),
                    ("Connection", "close"),
                ],
            )
        )
        writer.write(body)
        with contextlib.suppress(Exception):
            await writer.drain()
            writer.close()
            await writer.wait_closed()

    def _target_rows(self) -> list[dict[str, str]]:
        return [
            {
                "id": target.id,
                "title": target.title,
                "type": target.type,
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{self._port}/devtools/page/{target.id}",
            }
            for target in self.targets
        ]

    async def _upgrade(self, key: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(
            build_response_head(
                101,
                [("Upgrade", "websocket"), ("Connection", "Upgrade"), ("Sec-WebSocket-Accept", accept_key(key))],
            )
        )
        await writer.drain()
        self._writers.append(writer)
        try:
            await self._serve_frames(reader, writer)
        finally:
            if writer in self._writers:
                self._writers.remove(writer)
            with contextlib.suppress(Exception):
                writer.close()

    async def _serve_frames(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        fragments: list[bytes] = []
        while True:
            try:
                first_two = await reader.readexactly(2)
                remaining = header_length(first_two) - 2
                rest = await reader.readexactly(remaining) if remaining else b""
                header = parse_frame_header(first_two + rest)
                payload = apply_mask(await reader.readexactly(header.payload_length), header.mask)
            except (asyncio.IncompleteReadError, OSError):
                return
            if not header.masked:
                self.unmasked_client_frames += 1
            if header.is_control:
                if header.opcode == OPCODE_PING:
                    writer.write(build_frame(OPCODE_PONG, payload))
                    await writer.drain()
                    continue
                if header.opcode == OPCODE_PONG:
                    continue
                return
            fragments.append(payload)
            if not header.fin and header.opcode in (OPCODE_TEXT, OPCODE_CONTINUATION):
                continue
            message, fragments = b"".join(fragments), []
            await self._answer(json.loads(message.decode("utf-8")), writer)

    async def _answer(self, message: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        method = message.get("method", "")
        params = message.get("params", {})
        self.calls.append((method, params))

        if method and method == self.hold_method:
            await self.held.wait()

        handler = self.handlers.get(method)
        if handler is not None:
            result = handler(params)
        elif method == "Runtime.evaluate" and self.page is not None:
            result = self.page.evaluate(params)
        else:
            result = {}

        if isinstance(result, _Refusal):
            reply = {"id": message.get("id"), "error": {"code": -32000, "message": result.message}}
        elif result is _NO_REPLY:
            return
        else:
            reply = {"id": message.get("id"), "result": result}
        body = json.dumps(reply).encode("utf-8")
        if method and method == self.fragment_method:
            half = len(body) // 2
            writer.write(build_frame(OPCODE_TEXT, body[:half], fin=False))
            writer.write(build_frame(OPCODE_CONTINUATION, body[half:]))
        else:
            writer.write(build_frame(OPCODE_TEXT, body))
        await writer.drain()


@dataclass(frozen=True)
class _Refusal:
    """A handler's answer that the debugger refused the command."""

    message: str


def refuse(message: str) -> _Refusal:
    """A handler answer that comes back as a CDP ``error``."""
    return _Refusal(message)


class _NoReply:
    """A handler answer that sends nothing at all."""


_NO_REPLY = _NoReply()


def never_answer(_params: dict[str, Any]) -> Any:
    """A handler that leaves the caller waiting for ever."""
    return _NO_REPLY
