"""The loopback server — the port, the two routes, and what each one refuses.

Contract: the HTTP surface this backend offers and the WebSocket it upgrades to.
Owns the bind and its fallback, the static route, the upgrade handshake, and the
rule that the newest connection wins. It performs the admission checks by
calling :mod:`host.access`; it does not decide them.

**It is a hand-written server because the standard library has no asyncio HTTP
server** — ``http.server`` blocks a thread per request, and this backend is
asyncio throughout. What is written here is exactly as much HTTP as a ``GET``
and an upgrade need, and no more: no ``POST``, no body parsing, no directory
listings. Every call travels on the WebSocket.

**The directory served is handed in, never searched for.** A host that looked
for a build output relative to its own file would be the only piece of this
backend that knew the repository's layout, and it would be wrong the moment the
program was installed somewhere.

**The CORS header on the static route is load-bearing, not decoration.** The
panel bundle is fetched by a cross-origin ``import()``, which the browser makes
in CORS mode. Without ``Access-Control-Allow-Origin`` naming the asking origin
the bundle does not load slowly — it does not load at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import TYPE_CHECKING

from host.access import SESSION_PARAM, TOKEN_PARAM, AccessPolicy, check_access, new_token
from host.connection import HostConnection
from lib.http_messages import HEAD_TERMINATOR, MAX_HEAD_BYTES, HttpParseError, build_response_head, parse_request_head
from lib.path_safety import safe_join
from lib.websocket_frames import accept_key

if TYPE_CHECKING:
    import logging

    from host.dispatch import CallDispatcher
    from host.events import EventSink
    from lib.http_messages import RequestHead

# The port asked for first. Falling back is for a FOREIGN program holding it —
# a second copy of this backend never gets here, because the single-instance
# lock is taken before the bind is attempted.
DEFAULT_PORT = 27737
PORT_ATTEMPTS = 32

# The one path that upgrades. Everything else on this server is a file.
WS_PATH = "/ws"

# What the panel is loaded from. The host knows this name because the address it
# prints at start-up has to be complete enough to paste.
BUNDLE_FILENAME = "index.js"

_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}
_DEFAULT_CONTENT_TYPE = "application/octet-stream"


class HostServer:
    """Binds a loopback port and serves the panel: one file route, one upgrade."""

    def __init__(
        self,
        *,
        dispatcher: CallDispatcher,
        events: EventSink,
        static_root: str,
        logger: logging.Logger,
        server_identity: str,
        token: str | None = None,
        preferred_port: int = DEFAULT_PORT,
        port_attempts: int = PORT_ATTEMPTS,
    ) -> None:
        self._dispatcher = dispatcher
        self._events = events
        self._static_root = static_root
        self._logger = logger
        self._server_identity = server_identity
        self._token = token or new_token()
        self._preferred_port = preferred_port
        self._port_attempts = port_attempts

        self._server: asyncio.Server | None = None
        self._port = 0
        self._connection: HostConnection | None = None
        # Summed across connections rather than read off the live one. The case
        # this counter exists for is a panel and a backend that disagree about
        # the wire, and the development loop reaches it by swapping the frontend
        # — which is a NEW connection, so a per-connection count would reset at
        # exactly the moment it had something to report.
        self._dropped_by_closed_connections = 0

    @property
    def port(self) -> int:
        """The port actually bound; zero before :meth:`start`."""
        return self._port

    @property
    def token(self) -> str:
        """This process's admission token. Never served, never logged to file."""
        return self._token

    @property
    def bound_addresses(self) -> tuple[str, ...]:
        """The addresses this server is listening on; empty before :meth:`start`."""
        if self._server is None:
            return ()
        return tuple(sock.getsockname()[0] for sock in self._server.sockets)

    @property
    def connected(self) -> bool:
        """Is a panel connected right now?"""
        return self._connection is not None and not self._connection.closed

    @property
    def dropped_messages(self) -> int:
        """How many messages this SERVER could not make sense of, across every connection."""
        live = self._connection.dropped_messages if self._connection is not None else 0
        return self._dropped_by_closed_connections + live

    def asset_url(self, filename: str) -> str:
        """The complete address *filename* is served at, token included.

        The one place an address of this server is spelled. What loads the panel
        runs in this process and asks here, so the token reaches the code that
        needs it without ever being stored anywhere.
        """
        return f"http://127.0.0.1:{self._port}/{filename}?{TOKEN_PARAM}={self._token}"

    def bundle_url(self) -> str:
        """The complete address the panel bundle is loaded from, token included."""
        return self.asset_url(BUNDLE_FILENAME)

    async def start(self) -> int:
        """Bind a port and begin serving; return the port that was taken.

        Tries the preferred port first and then the ports above it. A fallback
        here means some other program holds the port — a second copy of this
        backend was refused by the lock long before it reached this line.
        """
        last_error: OSError | None = None
        for port in range(self._preferred_port, self._preferred_port + self._port_attempts):
            try:
                self._server = await asyncio.start_server(self._handle, host="127.0.0.1", port=port)
            except OSError as exc:
                last_error = exc
                continue
            self._port = port
            if port != self._preferred_port:
                self._logger.warning(f"host: port {self._preferred_port} was taken, listening on {port} instead")
            self._logger.info(f"host: listening on 127.0.0.1:{port}")
            return port

        raise OSError(
            f"no free port in {self._preferred_port}..{self._preferred_port + self._port_attempts - 1}"
        ) from last_error

    async def stop(self) -> None:
        """Stop accepting, and close every connection the server holds, upgraded or not."""
        if self._connection is not None:
            await self._connection.close(1001, "server shutting down")
            self._connection = None
        if self._server is not None:
            # A connection accepted in the previous tick is still a pending task
            # building its transport; the yield lets it attach, so close_clients()
            # reaches it. One whose accept lands in this same iteration can still
            # be abandoned half-made, and CPython 3.13 then raises one harmless
            # unraisable ``TypeError`` when the collector reaches that transport.
            await asyncio.sleep(0)
            self._server.close()
            # On 3.13 ``wait_closed`` waits for every client, so an accepted
            # connection that never sends its request head would hold it open.
            self._server.close_clients()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

    # -- request handling ------------------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one accepted socket: read the head, admit it, route it."""
        try:
            head = await self._read_head(reader)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError) as exc:
            self._logger.warning(f"host: unreadable request head: {exc}")
            await self._refuse(writer, 400)
            return
        except OSError:
            return

        policy = AccessPolicy(port=self._port, token=self._token)
        verdict = check_access(head, policy)
        if verdict.refused:
            self._logger.warning(f"host: {verdict.log_line}")
            await self._refuse(writer, verdict.status)
            return

        if head.method != "GET":
            await self._refuse(writer, 405)
            return

        if head.path == WS_PATH:
            await self._upgrade(head, reader, writer)
            return

        await self._serve_file(head, writer, verdict.echo_origin)

    async def _read_head(self, reader: asyncio.StreamReader) -> RequestHead:
        """Read up to the blank line and parse what came before it."""
        raw = await reader.readuntil(HEAD_TERMINATOR)
        if len(raw) > MAX_HEAD_BYTES:
            raise HttpParseError(f"request head of {len(raw)} bytes is over the {MAX_HEAD_BYTES} limit")
        return parse_request_head(raw[: -len(HEAD_TERMINATOR)])

    async def _refuse(self, writer: asyncio.StreamWriter, status: int) -> None:
        """Answer *status* with nothing in the body, and close.

        The ``Server`` field is the whole of what a refusal reveals: it says
        which program answered and nothing about why, which is the honest answer
        to a request that brought no token.
        """
        await self._write_response(writer, status, [("Content-Length", "0"), ("Connection", "close")])
        await self._shutdown(writer)

    async def _serve_file(self, head: RequestHead, writer: asyncio.StreamWriter, echo_origin: str) -> None:
        """Serve one file from the static root, or 404.

        Containment is :func:`lib.path_safety.safe_join`, which resolves symlinks
        before comparing — so neither a ``..`` segment, an absolute path, nor a
        link planted inside the root can name a file outside it. A path that
        does not resolve to a regular file inside the root is a 404 and never a
        description of what is there.
        """
        requested = head.path.lstrip("/") or BUNDLE_FILENAME
        loop = asyncio.get_running_loop()
        try:
            found = await loop.run_in_executor(None, _read_from_root, self._static_root, requested)
        except ValueError:
            self._logger.warning(f"host: refusing a path outside the served root: {head.path!r}")
            await self._refuse(writer, 404)
            return
        except OSError as exc:
            self._logger.error(f"host: cannot read {head.path!r} under the served root: {exc}")
            await self._refuse(writer, 500)
            return

        if found is None:
            await self._refuse(writer, 404)
            return
        body, content_type = found

        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-cache"),
            ("Connection", "close"),
        ]
        if echo_origin:
            # Named for the asking origin rather than ``*`` — and ``Vary`` with
            # it, because the answer genuinely differs by origin and a cache that
            # missed that would hand one origin another's header.
            headers.append(("Access-Control-Allow-Origin", echo_origin))
            headers.append(("Vary", "Origin"))

        await self._write_response(writer, 200, headers, body)
        await self._shutdown(writer)

    async def _upgrade(self, head: RequestHead, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Complete the WebSocket handshake and serve the connection.

        The newest connection always wins: an earlier one is closed here, as the
        replacement arrives. The session identity travels in the upgrade address
        rather than in a first message, which is what lets that decision be
        taken at connection time — the identity is the caller's own, one per
        bundle instance, so the log can tell a reconnect of the same panel from a
        leftover of an earlier one.
        """
        key = head.header("sec-websocket-key")
        upgrade = head.header("upgrade").lower()
        version = head.header("sec-websocket-version")
        # The key is answered with a digest over its ASCII bytes. A header is
        # decoded latin-1, so a non-ASCII one reaches here intact and would
        # raise out of this callback — asyncio prints a bare traceback and the
        # socket is simply left open. Refused as a malformed handshake instead.
        if upgrade != "websocket" or not key or not key.isascii() or version != "13":
            self._logger.warning(f"host: not a WebSocket 13 handshake (upgrade={upgrade!r}, version={version!r})")
            await self._refuse(writer, 426)
            return

        await self._write_response(
            writer,
            101,
            [
                ("Upgrade", "websocket"),
                ("Connection", "Upgrade"),
                ("Sec-WebSocket-Accept", accept_key(key)),
            ],
        )

        session_id = head.query.get(SESSION_PARAM, "")
        previous = self._connection
        if previous is not None and not previous.closed:
            same = "the same panel reconnecting" if previous.session_id == session_id else "an older panel"
            self._logger.info(f"host: a new connection displaces {same} (session {previous.session_id!r})")
            await previous.close(1001, "displaced by a newer connection")

        connection = HostConnection(reader, writer, self._dispatcher, self._logger, session_id=session_id)
        self._connection = connection
        sender = connection.send
        self._events.attach(sender)
        self._logger.info(f"host: panel connected (session {session_id!r})")
        try:
            await connection.run()
        finally:
            self._events.detach(sender)
            self._dropped_by_closed_connections += connection.dropped_messages
            if self._connection is connection:
                self._connection = None
            await self._shutdown(writer)

    async def _write_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        headers: list[tuple[str, str]],
        body: bytes = b"",
    ) -> None:
        """Write one response head, and a body if there is one."""
        with contextlib.suppress(ConnectionError, OSError, RuntimeError):
            writer.write(build_response_head(status, [("Server", self._server_identity), *headers]))
            if body:
                writer.write(body)
            await writer.drain()

    async def _shutdown(self, writer: asyncio.StreamWriter) -> None:
        """Close one socket, tolerating a peer that closed it first."""
        with contextlib.suppress(ConnectionError, OSError, RuntimeError):
            writer.close()
            await writer.wait_closed()


def _read_from_root(root: str, requested: str) -> tuple[bytes, str] | None:
    """Read *requested* from under *root*, or answer ``None`` if it is not a file there.

    Every filesystem question about a request is asked here, in one place, and
    run off the event loop — resolving a path stats it, and so does deciding
    whether it is a file. Raises :class:`lib.path_safety.PathTraversalError` when
    the request names something outside the root.
    """
    resolved = safe_join(root, requested)
    if not os.path.isfile(resolved):
        return None
    with open(resolved, "rb") as handle:
        body = handle.read()
    content_type = _CONTENT_TYPES.get(os.path.splitext(resolved)[1].lower(), _DEFAULT_CONTENT_TYPE)
    return body, content_type
