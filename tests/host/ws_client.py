"""A real client for the host's real socket.

The host's protocol is bytes on a socket, so the tier that checks it speaks
bytes on a socket. Nothing here mocks a stream: every helper opens a TCP
connection to the port the server actually bound, writes the handshake a browser
would write, and reads frames back through the same codec the server uses.

Client frames are masked, because RFC 6455 requires it of a client and the
server refuses an unmasked one — a helper that skipped the mask would be testing
a protocol nobody speaks.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any

from host.access import SESSION_PARAM, STEAM_UI_ORIGIN, TOKEN_PARAM
from lib.http_messages import HEAD_TERMINATOR, parse_request_head
from lib.websocket_frames import (
    OPCODE_PONG,
    OPCODE_TEXT,
    apply_mask,
    build_frame,
    header_length,
    parse_frame_header,
)

RECEIVE_TIMEOUT = 5.0


def _mask() -> bytes:
    return os.urandom(4)


async def http_get(
    port: int,
    path: str,
    *,
    token: str | None = None,
    origin: str | None = STEAM_UI_ORIGIN,
    host: str | None = None,
    method: str = "GET",
) -> tuple[int, dict[str, str], bytes]:
    """Make one request and return ``(status, headers, body)``.

    *path* is used verbatim, so a test can send a raw ``..`` or a percent-encoded
    one without this helper normalising it away — which would be the one thing
    the traversal cases must not have done for them.
    """
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    target = path if token is None else f"{path}{'&' if '?' in path else '?'}{TOKEN_PARAM}={token}"
    lines = [f"{method} {target} HTTP/1.1", f"Host: {host if host is not None else f'127.0.0.1:{port}'}"]
    if origin is not None:
        lines.append(f"Origin: {origin}")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
    await writer.drain()

    raw = await asyncio.wait_for(reader.readuntil(HEAD_TERMINATOR), RECEIVE_TIMEOUT)
    head, _, _ = raw.partition(HEAD_TERMINATOR)
    status_line, _, rest = head.decode("latin-1").partition("\r\n")
    status = int(status_line.split(" ")[1])
    headers = {
        name.strip().lower(): value.strip()
        for name, _, value in (line.partition(":") for line in rest.split("\r\n") if line)
    }

    length = int(headers.get("content-length", "0"))
    body = await asyncio.wait_for(reader.readexactly(length), RECEIVE_TIMEOUT) if length else b""
    writer.close()
    return status, headers, body


class WsTestClient:
    """A WebSocket client that speaks exactly what the server expects."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, handshake: dict[str, str]) -> None:
        self._reader = reader
        self._writer = writer
        self.handshake = handshake

    @classmethod
    async def connect(
        cls,
        port: int,
        token: str,
        *,
        session: str = "",
        origin: str | None = STEAM_UI_ORIGIN,
        host: str | None = None,
        version: str = "13",
    ) -> WsTestClient:
        """Perform the upgrade and return the connected client."""
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        target = f"/ws?{TOKEN_PARAM}={token}&{SESSION_PARAM}={session}"
        lines = [
            f"GET {target} HTTP/1.1",
            f"Host: {host if host is not None else f'127.0.0.1:{port}'}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode('ascii')}",
            f"Sec-WebSocket-Version: {version}",
        ]
        if origin is not None:
            lines.append(f"Origin: {origin}")
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        await writer.drain()

        raw = await asyncio.wait_for(reader.readuntil(HEAD_TERMINATOR), RECEIVE_TIMEOUT)
        head = parse_request_head(b"GET / HTTP/1.1\r\n" + raw.partition(b"\r\n")[2].rstrip(b"\r\n"))
        status = int(raw.partition(b"\r\n")[0].decode("latin-1").split(" ")[1])
        if status != 101:
            writer.close()
            raise AssertionError(f"the upgrade was refused with {status}")
        return cls(reader, writer, dict(head.headers))

    async def send_text(self, text: str) -> None:
        """Send one masked text frame."""
        self._writer.write(build_frame(OPCODE_TEXT, text.encode("utf-8"), mask=_mask()))
        await self._writer.drain()

    async def send_json(self, message: dict[str, Any]) -> None:
        """Send one message."""
        await self.send_text(json.dumps(message))

    async def send_raw(self, frame: bytes) -> None:
        """Send bytes the codec would never produce — for the refusal cases."""
        self._writer.write(frame)
        await self._writer.drain()

    async def call(self, call_id: Any, method: str, args: list[Any] | None = None) -> dict[str, Any]:
        """Send a call and wait for the message that answers it."""
        await self.send_json({"type": "call", "id": call_id, "method": method, "args": args or []})
        return await self.recv_json()

    async def recv_frame(self, timeout: float = RECEIVE_TIMEOUT) -> tuple[int, bytes]:
        """Read one whole frame; control frames are returned, not swallowed."""
        first_two = await asyncio.wait_for(self._reader.readexactly(2), timeout)
        remaining = header_length(first_two) - 2
        rest = await asyncio.wait_for(self._reader.readexactly(remaining), timeout) if remaining else b""
        header = parse_frame_header(first_two + rest)
        payload = await asyncio.wait_for(self._reader.readexactly(header.payload_length), timeout)
        return header.opcode, apply_mask(payload, header.mask)

    async def recv_json(self, timeout: float = RECEIVE_TIMEOUT) -> dict[str, Any]:
        """Read the next TEXT frame, answering any ping on the way."""
        while True:
            opcode, payload = await self.recv_frame(timeout)
            if opcode == OPCODE_TEXT:
                return json.loads(payload.decode("utf-8"))
            if opcode == 0x9:
                self._writer.write(build_frame(OPCODE_PONG, payload, mask=_mask()))
                await self._writer.drain()

    async def close(self) -> None:
        """Drop the connection without a close frame — what a page going away does."""
        self._writer.close()
