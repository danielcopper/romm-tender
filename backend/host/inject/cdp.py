"""The Chrome DevTools Protocol, as much of it as loading a panel needs.

Contract: talking to the CEF debugger — listing what it has open, holding one
connection to one target, sending commands and receiving events. It decides
nothing about what to send; that is the injector's.

**One reader owns the socket, and no wait here is a socket timeout.** Every
frame is read by a single task that resolves command futures and fans events
into subscribed queues, so waiting for an answer and waiting for an event are two
waits on two different awaitables, and neither is a property of the connection.
How long either may take is the caller's to bound, with ``asyncio.timeout``
around the await. The spike this replaces (``spike/inject.py``, #1897) set the
socket's own timeout to listen for an event and never put it back, so every later
command expired against the listen window — and an expired read left a
half-consumed frame behind, which no later reader could resynchronise with. That
failure is not available here: nothing but the reader task ever reads, so a caller
giving up on an answer costs the frame stream nothing.

**Client frames are masked, server frames are not** (RFC 6455 §5.1), and both
halves come from :mod:`lib.websocket_frames` rather than from a hand-rolled codec
of this module's own. ``adapters/renderer_gc.py`` carries one, and predates that
module; it is not a pattern to copy.

**No ``except`` below names a class another already covers**, so each one is the
whole of what it catches rather than a list of what was expected. The three that
would read as documentation are language facts: ``TimeoutError`` — what an
``asyncio.timeout`` window closing and a socket's own ``timeout=`` both raise —
is an ``OSError``, ``urllib``'s ``URLError`` is one too, and
``UnicodeDecodeError`` is a ``ValueError``. So a deadline this module set lands
in an ``OSError`` clause, and a payload that is not UTF-8 in a ``ValueError``
one; naming either beside its base would widen nothing.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import struct
from dataclasses import dataclass
from itertools import count
from typing import TYPE_CHECKING, Any
from urllib.request import urlopen

from lib.http_messages import HEAD_TERMINATOR
from lib.websocket_frames import (
    CLOSE_NORMAL,
    OPCODE_CLOSE,
    OPCODE_CONTINUATION,
    OPCODE_PING,
    OPCODE_PONG,
    OPCODE_TEXT,
    WebSocketProtocolError,
    accept_key,
    apply_mask,
    build_frame,
    header_length,
    parse_frame_header,
)

if TYPE_CHECKING:
    import logging

# Where Steam's CEF debugger listens once
# ``~/.steam/steam/.cef-enable-remote-debugging`` exists. The same port the rest
# of this repo already drives (``adapters/renderer_gc.py``,
# ``scripts/dev_ui_scale.py``); on the reference machine ``steamwebhelper``
# itself holds ``127.0.0.1:8080``.
DEBUGGER_PORT = 8080

# The target that renders Steam's own interface, and the one the panel is loaded
# into. Steam names it this AFTER creating it — see ``find_shared_context``.
SHARED_JS_CONTEXT = "SharedJSContext"

# A page the debugger lists. The alive check counts these; ``SHARED_JS_CONTEXT``
# is excluded from that count by its target id rather than by its type, so the
# count does not depend on a second reading of the title.
PAGE_TARGET_TYPE = "page"

# The largest message this client will assemble from the debugger. The debugger
# is another program, and nothing we ask it for comes close — an answer over this
# means something is wrong on the other side, and the connection is dropped
# rather than the buffer grown.
MAX_MESSAGE_BYTES = 16 * 1024 * 1024

_HANDSHAKE_TIMEOUT = 5.0


class CdpUnavailableError(Exception):
    """Raised when the debugger could not be reached or did not answer usably."""


class CdpConnectionLost(Exception):
    """Raised for a command whose connection went before the answer came back."""


@dataclass(frozen=True)
class Target:
    """One entry of the debugger's target list."""

    id: str
    title: str
    type: str
    websocket_url: str


def parse_targets(payload: object) -> tuple[Target, ...]:
    """Turn the debugger's ``/json`` answer into targets, skipping unusable rows.

    A row with no id or no WebSocket address cannot be connected to, so it is
    dropped rather than carried as a target that would fail later. An absent
    title is kept as the empty string, which is a target Steam has created and
    not yet named.
    """
    if not isinstance(payload, list):
        raise CdpUnavailableError(f"the debugger's target list is a {type(payload).__name__}, not a list")
    targets: list[Target] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        identity = row.get("id")
        url = row.get("webSocketDebuggerUrl")
        if not isinstance(identity, str) or not isinstance(url, str):
            continue
        title = row.get("title")
        kind = row.get("type")
        targets.append(
            Target(
                id=identity,
                title=title if isinstance(title, str) else "",
                type=kind if isinstance(kind, str) else "",
                websocket_url=url,
            )
        )
    return tuple(targets)


async def list_targets(port: int = DEBUGGER_PORT, *, read_timeout: float = 2.0) -> tuple[Target, ...]:
    """Ask the debugger on *port* what it has open.

    *read_timeout* bounds the blocking request itself rather than this
    coroutine: the fetch runs on a worker thread, where an ``asyncio`` deadline
    would return control without stopping the read.

    Raises :class:`CdpUnavailableError` when nothing answers, or when what
    answered is not a target list — which is what a program other than Steam
    holding the port looks like.
    """
    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(None, _fetch_target_list, port, read_timeout)
    except OSError as exc:
        raise CdpUnavailableError(f"the debugger on 127.0.0.1:{port} did not answer: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise CdpUnavailableError(f"the debugger on 127.0.0.1:{port} answered something that is not JSON") from exc
    return parse_targets(payload)


def _fetch_target_list(port: int, timeout: float) -> bytes:
    """Read ``/json`` off the debugger, on a worker thread.

    ``urlopen`` blocks, so it runs off the event loop. The address is built here
    from a port rather than taken from a caller, which is what keeps this from
    being a general fetcher.
    """
    with urlopen(f"http://127.0.0.1:{port}/json", timeout=timeout) as response:  # loopback, address built here
        return response.read()


def find_shared_context(targets: tuple[Target, ...]) -> Target | None:
    """The ``SharedJSContext`` target, or ``None`` because it is not named yet.

    **An answer of ``None`` is never "Steam has nothing open".** Measured on the
    device: from the debugger port answering, a target appears at +0.20 s with an
    EMPTY title and the SAME target is renamed ``SharedJSContext`` at +0.6 s. A
    caller that read the first miss as a verdict would give up half a second
    before the answer existed, so every caller here retries.
    """
    for target in targets:
        if target.title == SHARED_JS_CONTEXT:
            return target
    return None


def unnamed_targets(targets: tuple[Target, ...]) -> tuple[Target, ...]:
    """Targets the debugger lists with no title yet.

    Worth telling apart from an empty list in a log: one says Steam is coming up,
    the other says nothing is there.
    """
    return tuple(target for target in targets if not target.title)


def pages_besides(targets: tuple[Target, ...], target_id: str) -> tuple[Target, ...]:
    """Page targets other than the one identified by *target_id*.

    The crash this counts for takes every page target down at once and leaves
    ``SharedJSContext`` answering, so what says the interface is still there is
    the presence of something else. The one to discount is named by ID, because
    that is what the caller injected into.
    """
    return tuple(target for target in targets if target.type == PAGE_TARGET_TYPE and target.id != target_id)


def split_ws_url(url: str) -> tuple[str, int, str]:
    """Split ``ws://host:port/path`` into its three parts; the port defaults to 80."""
    rest = url.split("://", 1)[-1]
    authority, _, path = rest.partition("/")
    host, _, port = authority.partition(":")
    return host, int(port) if port.isdigit() else 80, "/" + path


class CdpConnection:
    """One open WebSocket to one debugger target.

    Commands are awaited by id; events are delivered to whoever subscribed to
    them by name, and to nobody otherwise. Both ends of the connection's life are
    explicit: :meth:`connect` opens it, :meth:`close` ends it, and
    :attr:`closed` answers in between.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, logger: logging.Logger) -> None:
        self._reader = reader
        self._writer = writer
        self._logger = logger
        self._ids = count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._subscriptions: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}
        self._closed = False
        self._pump: asyncio.Task[None] | None = None

    @classmethod
    async def connect(cls, url: str, *, logger: logging.Logger) -> CdpConnection:
        """Open and hand back a connection to the target at *url*.

        The handshake carries its own bound rather than taking one, because it
        is a property of the protocol and not of what the caller wants to do
        afterwards.

        Raises :class:`CdpUnavailableError` when the socket cannot be opened or
        the handshake is not answered the way RFC 6455 requires.
        """
        host, port, path = split_ws_url(url)
        try:
            async with asyncio.timeout(_HANDSHAKE_TIMEOUT):
                reader, writer = await asyncio.open_connection(host, port)
        except OSError as exc:
            raise CdpUnavailableError(f"cannot reach the debugger at {url}: {exc}") from exc

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        writer.write(request.encode("ascii"))
        try:
            async with asyncio.timeout(_HANDSHAKE_TIMEOUT):
                await writer.drain()
                head = await reader.readuntil(HEAD_TERMINATOR)
        except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            writer.close()
            raise CdpUnavailableError(f"the debugger did not complete the handshake at {url}: {exc}") from exc

        if not _handshake_accepted(head, key):
            writer.close()
            raise CdpUnavailableError(f"the debugger refused the WebSocket handshake at {url}")

        connection = cls(reader, writer, logger)
        connection._pump = asyncio.create_task(connection._read_loop())
        return connection

    @property
    def closed(self) -> bool:
        """Has this connection stopped carrying anything?"""
        return self._closed

    def subscribe(self, method: str) -> asyncio.Queue[dict[str, Any] | None]:
        """Ask for every later event named *method*, and hand back its queue.

        Subscribing before the event can happen is what keeps one from being
        missed while a command is in flight: the reader puts it on the queue
        whether or not anybody is waiting on it right then. The queue receives
        ``None`` once when the connection ends, so a waiter learns of that
        without a second thing to watch.
        """
        queue = self._subscriptions.get(method)
        if queue is None:
            queue = asyncio.Queue()
            self._subscriptions[method] = queue
        return queue

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one command and answer with its ``result``.

        It waits for as long as the debugger takes. How long that may be is the
        caller's question — a command that loads a panel and one that reads a
        boolean are not the same wait — so the bound belongs at the call site,
        as an ``asyncio.timeout`` around the await. Giving up costs this
        connection nothing: the pending entry is dropped and the reader task
        carries on owning the frame stream.

        Raises :class:`CdpConnectionLost` when the connection goes before the
        answer arrives, and :class:`CdpUnavailableError` when the debugger
        answers the command with an error.
        """
        if self._closed:
            raise CdpConnectionLost(f"the debugger connection is gone; {method} was not sent")
        call_id = next(self._ids)
        waiting: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[call_id] = waiting
        try:
            await self._send(json.dumps({"id": call_id, "method": method, "params": params or {}}))
            answer = await waiting
        finally:
            self._pending.pop(call_id, None)

        if "error" in answer:
            raise CdpUnavailableError(f"{method} was refused: {answer['error']}")
        result = answer.get("result")
        return result if isinstance(result, dict) else {}

    async def close(self) -> None:
        """End the connection, and release everything waiting on it."""
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(OSError, RuntimeError):
            # Built here rather than with ``close_frame``, which builds the
            # UNMASKED frame a server sends. Every frame this side puts on the
            # wire is a client frame and has to be masked (RFC 6455 §5.1).
            self._writer.write(build_frame(OPCODE_CLOSE, struct.pack("!H", CLOSE_NORMAL), mask=os.urandom(4)))
            await self._writer.drain()
        with contextlib.suppress(OSError, RuntimeError):
            self._writer.close()
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
        self._release_waiters()

    # -- the one reader --------------------------------------------------------

    async def _read_loop(self) -> None:
        """Read frames until the connection ends, routing each message."""
        fragments: list[bytes] = []
        buffered = 0
        try:
            while True:
                header, payload = await self._next_frame()
                if header.opcode == OPCODE_CLOSE:
                    return
                if header.opcode == OPCODE_PING:
                    await self._write_frame(build_frame(OPCODE_PONG, payload, mask=os.urandom(4)))
                    continue
                if header.opcode == OPCODE_PONG:
                    continue
                if header.opcode not in (OPCODE_TEXT, OPCODE_CONTINUATION):
                    raise WebSocketProtocolError(f"the debugger sent opcode {header.opcode:#x}")

                fragments.append(payload)
                buffered += len(payload)
                if buffered > MAX_MESSAGE_BYTES:
                    raise WebSocketProtocolError(f"a message of {buffered} bytes is over the {MAX_MESSAGE_BYTES} cap")
                if not header.fin:
                    continue
                message, fragments, buffered = b"".join(fragments), [], 0
                self._route(message)
        except (OSError, asyncio.IncompleteReadError, WebSocketProtocolError) as exc:
            self._logger.info(f"inject: the debugger connection ended ({type(exc).__name__}: {exc})")
        finally:
            self._closed = True
            self._release_waiters()

    async def _next_frame(self):
        """Read one whole frame off the socket."""
        first_two = await self._reader.readexactly(2)
        remaining = header_length(first_two) - 2
        rest = await self._reader.readexactly(remaining) if remaining else b""
        header = parse_frame_header(first_two + rest)
        if header.payload_length > MAX_MESSAGE_BYTES:
            raise WebSocketProtocolError(f"a frame of {header.payload_length} bytes is over the cap")
        payload = await self._reader.readexactly(header.payload_length) if header.payload_length else b""
        return header, apply_mask(payload, header.mask)

    def _route(self, raw: bytes) -> None:
        """Hand one decoded message to whoever is waiting for it.

        A message that is neither an answer we are waiting for nor an event
        somebody subscribed to is dropped in silence: the debugger emits events
        for every domain that has been enabled, and logging each one would bury
        the lines this module writes on purpose.
        """
        try:
            message = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            self._logger.warning(f"inject: the debugger sent something that is not a CDP message: {exc}")
            return
        if not isinstance(message, dict):
            return

        call_id = message.get("id")
        if isinstance(call_id, int):
            waiting = self._pending.get(call_id)
            if waiting is not None and not waiting.done():
                waiting.set_result(message)
            return

        queue = self._subscriptions.get(message.get("method", ""))
        if queue is not None:
            queue.put_nowait(message)

    def _release_waiters(self) -> None:
        """Fail every pending command and tell every subscriber the line is dead."""
        for waiting in self._pending.values():
            if not waiting.done():
                waiting.set_exception(CdpConnectionLost("the debugger connection ended before the answer arrived"))
        self._pending.clear()
        for queue in self._subscriptions.values():
            queue.put_nowait(None)

    async def _send(self, text: str) -> None:
        """Write one masked text frame."""
        await self._write_frame(build_frame(OPCODE_TEXT, text.encode("utf-8"), mask=os.urandom(4)))

    async def _write_frame(self, frame: bytes) -> None:
        try:
            self._writer.write(frame)
            await self._writer.drain()
        except (OSError, RuntimeError) as exc:
            self._closed = True
            raise CdpConnectionLost(f"the debugger connection went while writing: {exc}") from exc


def _handshake_accepted(head: bytes, key: str) -> bool:
    """Did the server answer this handshake the way RFC 6455 §4.2.2 requires?"""
    text = head.decode("latin-1")
    status = text.split("\r\n", 1)[0]
    if "101" not in status:
        return False
    expected = accept_key(key).lower()
    return any(
        line.lower().startswith("sec-websocket-accept:") and line.split(":", 1)[1].strip().lower() == expected
        for line in text.split("\r\n")
    )
