"""One live panel connection — frames in, answers out, and what a loss costs.

Contract: everything that is true of a single WebSocket for as long as it lives.
Reads frames, assembles messages, hands calls to the dispatcher, writes answers
and events back, and keeps the connection honest with a heartbeat. It knows
nothing about HTTP, admission or ports — by the time one of these exists, the
handshake has already happened.

**The size cap is checked before a byte of payload is read.** A frame announces
its length in its header, so an oversized frame is refused while it is still on
the network. A cap applied after assembly protects no memory at all — it
measures what has already been spent. The running total across a fragmented
message is checked the same way, for the same reason.

**Losing the connection rejects every call in flight.** A call whose answer was
still being computed when the socket went can never be answered, so its task is
cancelled rather than left to finish into a write that goes nowhere. The caller
learns of it from its own side: its pending register answers those calls
``connection_lost`` (``host.protocol``), which is a different sentence from a
backend failure and is what stops a panel waiting forever for an answer that is
never coming. There is deliberately **no reply store**: nothing is held for
redelivery after a reconnect. Over loopback a connection breaks essentially only
when the page itself goes away, the expensive apply path is already protected by
chunking and acknowledgement, and the dangerous calls carry claims. "No store"
means a lost answer fails visibly — never that it vanishes quietly.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from host.protocol import TYPE_CALL, decode_message
from lib.websocket_frames import (
    CLOSE_GOING_AWAY,
    CLOSE_MESSAGE_TOO_BIG,
    CLOSE_NORMAL,
    CLOSE_PROTOCOL_ERROR,
    CLOSE_UNSUPPORTED_DATA,
    OPCODE_CLOSE,
    OPCODE_CONTINUATION,
    OPCODE_PING,
    OPCODE_PONG,
    OPCODE_TEXT,
    WebSocketProtocolError,
    apply_mask,
    build_frame,
    close_frame,
    header_length,
    parse_frame_header,
    text_frame,
)

if TYPE_CHECKING:
    import logging

    from host.dispatch import CallDispatcher
    from lib.websocket_frames import FrameHeader

# The largest frame, and the largest assembled message, this connection will
# accept — a memory guard and nothing else. Distinct from the dispatcher's
# answer cap on purpose: breaking this one is a protocol violation and closes
# the socket, which rejects every call in flight, so it must sit far above any
# size a legitimate answer could reach.
MAX_FRAME_BYTES = 16 * 1024 * 1024

# The heartbeat goes out from the server, because the browser's ``WebSocket``
# API can neither send a ping nor observe a pong — it answers one automatically
# and says nothing about it. The other direction needs nothing: on loopback TCP
# fails promptly and the close event is enough.
HEARTBEAT_INTERVAL = 30.0
HEARTBEAT_TIMEOUT = 75.0


class HostConnection:
    """A single WebSocket to the panel: reads calls, writes replies and events."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        dispatcher: CallDispatcher,
        logger: logging.Logger,
        session_id: str = "",
        frame_limit: int = MAX_FRAME_BYTES,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
        heartbeat_timeout: float = HEARTBEAT_TIMEOUT,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._dispatcher = dispatcher
        self._logger = logger
        self._session_id = session_id
        self._frame_limit = frame_limit
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_timeout = heartbeat_timeout

        self._write_lock = asyncio.Lock()
        self._calls: set[asyncio.Task[None]] = set()
        self._closed = False
        self._dropped_messages = 0
        self._last_pong = 0.0

    @property
    def session_id(self) -> str:
        """The identity the caller claimed for itself when it connected."""
        return self._session_id

    @property
    def dropped_messages(self) -> int:
        """How many messages this connection could not make sense of."""
        return self._dropped_messages

    @property
    def closed(self) -> bool:
        """Has this connection stopped accepting writes?"""
        return self._closed

    async def send(self, text: str) -> bool:
        """Write one text message; answer whether it reached the socket.

        Serialised against every other write on this connection — two answers
        interleaving their frames would produce two messages neither end could
        parse. A write onto a connection that has gone answers ``False`` rather
        than raising, because every caller here is already handling "nobody
        heard this".
        """
        if self._closed:
            return False
        try:
            async with self._write_lock:
                if self._closed:
                    return False
                self._writer.write(text_frame(text))
                await self._writer.drain()
        except (RuntimeError, OSError) as exc:
            self._logger.info(f"host: write failed, the connection is gone ({type(exc).__name__}: {exc})")
            self._closed = True
            return False
        return True

    async def run(self) -> None:
        """Serve this connection until it closes, then reject what was in flight."""
        loop = asyncio.get_running_loop()
        self._last_pong = loop.time()
        heartbeat = asyncio.create_task(self._heartbeat())
        try:
            await self._read_loop()
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            await self._reject_calls_in_flight()
            await self.close(CLOSE_NORMAL)

    async def close(self, code: int, reason: str = "") -> None:
        """Send a close frame if the socket still takes one, then shut it down."""
        if self._closed:
            return
        self._closed = True
        try:
            async with self._write_lock:
                self._writer.write(close_frame(code, reason))
                await self._writer.drain()
        except (RuntimeError, OSError):
            pass
        with contextlib.suppress(ConnectionError, RuntimeError, OSError):
            self._writer.close()

    # -- reading ---------------------------------------------------------------

    async def _read_loop(self) -> None:
        """Read frames until the peer closes, breaks the protocol, or goes away."""
        fragments: list[bytes] = []
        buffered = 0

        while not self._closed:
            frame = await self._next_frame(buffered)
            if frame is None:
                return
            header, payload = frame

            if header.is_control:
                if await self._handle_control(header.opcode, payload):
                    return
                continue

            if not await self._accepts_data_frame(header, assembling=bool(fragments)):
                return

            fragments.append(payload)
            buffered += len(payload)
            if not header.fin:
                continue

            message, fragments, buffered = b"".join(fragments), [], 0
            self._handle_text(message)

    async def _next_frame(self, buffered: int) -> tuple[FrameHeader, bytes] | None:
        """Read one whole frame, or answer ``None`` because the connection is over.

        Every way reading a frame can end the connection is answered here, so
        the loop above sees a frame or an ending and nothing else. *buffered* is
        how much of the message being assembled is already held, which is what
        the cap is judged against across fragments.
        """
        try:
            header = await self._read_header()
        except (asyncio.IncompleteReadError, OSError):
            self._logger.info("host: the panel connection went away")
            return None
        except WebSocketProtocolError as exc:
            self._logger.warning(f"host: malformed frame, closing: {exc}")
            await self.close(CLOSE_PROTOCOL_ERROR, str(exc))
            return None

        if not header.masked:
            self._logger.warning("host: a client frame arrived unmasked, closing")
            await self.close(CLOSE_PROTOCOL_ERROR, "client frames must be masked")
            return None

        # The cap, judged on what the sender ANNOUNCED — the payload is
        # still on the network at this point and never enters this process.
        running = buffered + header.payload_length
        if header.payload_length > self._frame_limit or running > self._frame_limit:
            self._logger.warning(f"host: a frame of {running} bytes is over the {self._frame_limit} cap, closing")
            await self.close(CLOSE_MESSAGE_TOO_BIG, "frame over the size limit")
            return None

        try:
            payload = apply_mask(await self._reader.readexactly(header.payload_length), header.mask)
        except (asyncio.IncompleteReadError, OSError):
            self._logger.info("host: the panel connection went away mid-frame")
            return None
        return header, payload

    async def _accepts_data_frame(self, header: FrameHeader, *, assembling: bool) -> bool:
        """May this data frame join the message being assembled? Close if not.

        A message is begun by a text frame and carried on by continuations, so
        the three answers here are the three ways a data frame can contradict
        what is already being assembled — and the reason nothing downstream has
        to ask an assembled message which opcode began it.
        """
        if header.opcode == OPCODE_CONTINUATION:
            if assembling:
                return True
            await self.close(CLOSE_PROTOCOL_ERROR, "continuation with nothing to continue")
            return False

        if header.opcode == OPCODE_TEXT:
            if not assembling:
                return True
            await self.close(CLOSE_PROTOCOL_ERROR, "a new message began before the last one ended")
            return False

        self._logger.warning("host: a binary frame arrived; this host speaks text, closing")
        await self.close(CLOSE_UNSUPPORTED_DATA, "this host speaks text frames only")
        return False

    async def _read_header(self):
        """Read exactly one frame header off the socket and parse it."""
        first_two = await self._reader.readexactly(2)
        remaining = header_length(first_two) - 2
        rest = await self._reader.readexactly(remaining) if remaining else b""
        return parse_frame_header(first_two + rest)

    async def _handle_control(self, opcode: int, payload: bytes) -> bool:
        """Answer a control frame; return True when the connection should end."""
        if opcode == OPCODE_CLOSE:
            await self.close(CLOSE_NORMAL)
            return True
        if opcode == OPCODE_PING:
            async with self._write_lock:
                if not self._closed:
                    self._writer.write(build_frame(OPCODE_PONG, payload))
                    await self._writer.drain()
            return False
        if opcode == OPCODE_PONG:
            self._last_pong = asyncio.get_running_loop().time()
        return False

    def _handle_text(self, raw: bytes) -> None:
        """Turn one assembled text message into work, or drop it loudly.

        A message this host cannot act on is dropped and the connection is
        **kept**. Closing would reject every other call in flight, which is a
        heavy punishment for one malformed message — and the case is reachable
        in the development loop, where the frontend is swapped without the
        backend restarting.
        """
        try:
            message = decode_message(raw.decode("utf-8"))
        except ValueError as exc:
            self._dropped_messages += 1
            self._logger.warning(f"host: dropping a message that is not a protocol message: {exc}")
            return

        kind = message.get("type")
        if kind != TYPE_CALL:
            self._dropped_messages += 1
            self._logger.warning(f"host: dropping a message of type {kind!r} (id {message.get('id')!r})")
            return

        call_id = message.get("id")
        method = message.get("method")
        args = message.get("args", [])
        if call_id is None or not isinstance(method, str) or not isinstance(args, list):
            self._dropped_messages += 1
            self._logger.warning(f"host: dropping a malformed call (id {call_id!r}, method {method!r})")
            return

        self._start_call(call_id, method, args)

    def _start_call(self, call_id: Any, method: str, args: list[Any]) -> None:
        """Run one call as a task, holding a strong reference until it finishes."""
        task = asyncio.create_task(self._run_call(call_id, method, args))
        self._calls.add(task)
        task.add_done_callback(self._calls.discard)

    async def _run_call(self, call_id: Any, method: str, args: list[Any]) -> None:
        await self.send(await self._dispatcher.dispatch(call_id, method, args))

    async def _reject_calls_in_flight(self) -> None:
        """Cancel every call still running for this connection, and say how many.

        Their answers have nowhere to go, so finishing them would spend the
        machine on a write that cannot land — and one of them holding a
        transaction open would do it while the next connection waits on the same
        database.
        """
        pending = [task for task in self._calls if not task.done()]
        if not pending:
            return
        self._logger.info(f"host: the connection went with {len(pending)} call(s) in flight; cancelling them")
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def _heartbeat(self) -> None:
        """Ping on an interval and close a connection that has stopped answering."""
        loop = asyncio.get_running_loop()
        while not self._closed:
            await asyncio.sleep(self._heartbeat_interval)
            if self._closed:
                return
            if loop.time() - self._last_pong > self._heartbeat_timeout:
                self._logger.info("host: no pong within the heartbeat window, closing the connection")
                await self.close(CLOSE_GOING_AWAY, "heartbeat timeout")
                return
            try:
                async with self._write_lock:
                    if self._closed:
                        return
                    self._writer.write(build_frame(OPCODE_PING, b""))
                    await self._writer.drain()
            except (RuntimeError, OSError):
                self._closed = True
                return
